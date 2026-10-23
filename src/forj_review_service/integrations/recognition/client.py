"""Qwen 文档识别客户端。

主要职责：发送受控模型请求并校验、过滤结构化提取结果。
修改日期：2026-08-26
"""

import asyncio
import json
import logging
import re
import threading
import weakref
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.materials import (
    DocumentPolicy,
)
from forj_review_service.businesses.recognition_prompts import (
    PROMPT_VERSION,
    build_recognition_prompt,
)
from forj_review_service.config.settings import QwenConfig
from forj_review_service.contracts.recognition import (
    MaterialFact,
    QwenExtraction,
    valid_normalized_box,
)

# Bootstrap routes the whole application namespace to the same console/file sinks.
logger = logging.getLogger(__name__)


class QwenResponseSyntaxError(ValueError):
    """模型响应包含 JSON 外形，但语法无法解析。"""


class QwenResponseSchemaError(ValueError):
    """JSON 已成功解析，但内容不满足提取结果的数据结构。"""

    def __init__(self, message: str, *, detail: str = "unknown") -> None:
        """保存安全错误消息及供内部日志使用的有界结构详情。"""
        super().__init__(message)
        self.detail = detail


class QwenResponseStructureError(ValueError):
    """模型响应没有且仅有一个 JSON 对象。"""


class QwenResponseTruncatedError(RuntimeError):
    """The provider explicitly ended generation at its output limit."""


FIELD_LABEL_ECHOES: dict[str, frozenset[str]] = {
    "old_vehicle.recycle_date": frozenset({"交车日期", "报废交车日期"}),
    "scrap_certificate.certificate_no": frozenset({"回收证明编号", "报废证明编号"}),
    "invoice.invoice_no": frozenset({"发票号码", "数电号码"}),
    "invoice.amount": frozenset({"价税合计", "价税合计小写", "价税合计（小写）", "开票金额"}),
    "invoice.invoice_date": frozenset({"开票日期"}),
    "new_vehicle.origin": frozenset({"产地"}),
    "invoice.terminal_certificate_no": frozenset(
        {"统一社会信用代码", "纳税人识别号", "终端证件号"}
    ),
    "invoice.phone": frozenset({"电话", "终端客户手机号"}),
    "vehicle.type": frozenset({"车辆类型"}),
    "vehicle.vin": frozenset({"车辆识别代号", "车辆识别代号/车架号码", "车架号码"}),
    "vehicle.plate_no": frozenset({"号牌号码", "车牌号"}),
    "vehicle.owner": frozenset({"所有人", "机动车所有人", "购买方名称"}),
    "vehicle.engine_model": frozenset({"发动机型号"}),
    "vehicle.fuel_type": frozenset({"燃料种类", "燃料类型"}),
    "vehicle.emission_standard": frozenset({"排放标准"}),
    "vehicle.brand": frozenset({"车辆品牌", "厂牌"}),
    "vehicle.registration_date": frozenset({"注册日期"}),
}


def _int_or_zero(value: Any) -> int:
    """把用量字段读成整数；模型没返回或返回非数字时按 0 计，不影响审核结论。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _drop_field_name_echoes(normalized: dict[str, Any]) -> None:
    """Drop model placeholders such as {"invoice.code": "invoice.code"}."""
    fields = normalized.get("fields")
    if not isinstance(fields, dict):
        return

    def is_reading(field: str, value: Any) -> bool:
        return not (
            isinstance(value, str)
            and (
                value.strip() == field
                or value.strip() in FIELD_LABEL_ECHOES.get(field, frozenset())
            )
        )

    normalized["fields"] = {
        field: value for field, value in fields.items() if is_reading(field, value)
    }
    alternatives = normalized.get("field_alternatives")
    if isinstance(alternatives, dict):
        filtered = {}
        for field, values in alternatives.items():
            if not isinstance(values, list):
                filtered[field] = values  # Preserve schema errors for the validator.
                continue
            readings = [value for value in values if is_reading(field, value)]
            if readings:
                normalized["fields"].setdefault(field, readings[0])
                if len(readings) > 1:
                    filtered[field] = readings
        normalized["field_alternatives"] = filtered


def restrict_extraction_to_policy(
    extraction: QwenExtraction,
    policy: DocumentPolicy,
    business_scope: str,
) -> QwenExtraction:
    """只保留当前材料和业务范围白名单中的字段及证据位置。"""
    allowed = set(policy.fields_for_scope(business_scope))
    fields = {field: value for field, value in extraction.fields.items() if field in allowed}
    uncertain_fields = list(
        dict.fromkeys(field for field in extraction.uncertain_fields if field in allowed)
    )
    evidence_regions = [region for region in extraction.evidence_regions if region.field in allowed]
    return extraction.model_copy(
        update={
            "fields": fields,
            "uncertain_fields": uncertain_fields,
            "evidence_regions": evidence_regions,
            "field_alternatives": {
                key: values
                for key, values in extraction.field_alternatives.items()
                if key in allowed
            },
            "field_confidences": {
                key: value for key, value in extraction.field_confidences.items() if key in allowed
            },
        }
    )


def parse_qwen_extraction(content: str) -> QwenExtraction:
    """从模型响应中解析唯一 JSON 对象，并校验为结构化提取结果。"""
    # 兼容模型偶尔忽略“仅输出 JSON”约束而返回 Markdown 代码块的情况。
    fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
    if fenced_blocks:
        candidates = [_decode_json_object(block.strip()) for block in fenced_blocks]
    else:
        candidates = []
        decoder = json.JSONDecoder()
        offset = 0
        while True:
            object_start = content.find("{", offset)
            if object_start < 0:
                break
            try:
                value, end = decoder.raw_decode(content[object_start:])
            except json.JSONDecodeError:
                offset = object_start + 1
                continue
            candidates.append(value)
            offset = object_start + end

    if len(candidates) != 1:
        # 多个对象存在歧义，禁止自行选择其中一个作为审核依据。
        if not candidates and "{" in content:
            raise QwenResponseSyntaxError("JSON 对象解码失败")
        raise QwenResponseStructureError("响应中的 JSON 对象不唯一或不存在")
    value = candidates[0]
    if not isinstance(value, dict):
        raise QwenResponseStructureError("响应中的 JSON 不是对象")
    normalized = normalize_material_facts(dict(value))
    fields = normalized.get("fields")
    if fields is None:
        normalized["fields"] = {}
    elif isinstance(fields, dict):
        normalized["fields"] = {
            field: field_value for field, field_value in fields.items() if field_value is not None
        }

    for collection_name in ("evidence_regions", "uncertain_fields"):
        if normalized.get(collection_name) is None:
            normalized[collection_name] = []
    uncertain_fields = normalized.get("uncertain_fields")
    if isinstance(uncertain_fields, str):
        uncertain_field = uncertain_fields.strip()
        normalized["uncertain_fields"] = [uncertain_field] if uncertain_field else []
    _drop_field_name_echoes(normalized)

    try:
        return QwenExtraction.model_validate(normalized)
    except ValidationError as exc:
        details = []
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ())) or "root"
            error_type = str(item.get("type", "unknown"))
            details.append(f"{location}:{error_type}")
        raise QwenResponseSchemaError(
            "响应 JSON 不符合字段结构",
            detail=",".join(details[:5]),
        ) from exc


def _decode_json_object(content: str) -> dict[str, Any]:
    """解码 JSON，并确保顶层结构为对象。"""
    value = json.loads(content)
    if not isinstance(value, dict):
        raise TypeError("响应中的 JSON 不是对象")
    return value


def normalize_material_facts(value: dict[str, Any]) -> dict[str, Any]:
    """Project the compact model protocol; legacy recorded results remain readable."""
    if "facts" not in value:
        return value
    required = (
        "document_type",
        "type_confidence",
        "confidence",
        "identity_sides",
        "uncertain_fields",
    )
    if any(key not in value or value[key] is None for key in required):
        raise QwenResponseSchemaError("事实响应缺少类型或质量信息", detail="facts:missing_metadata")
    if any(value.get(key) for key in ("fields", "raw_fields", "evidence_regions")):
        raise QwenResponseSchemaError("响应同时包含两套事实结构", detail="facts:ambiguous")
    facts = value["facts"]
    if not isinstance(facts, list) or len(facts) > 300:
        raise QwenResponseSchemaError("facts 必须是有界对象数组", detail="facts:list_type")
    fields, alternatives, rows, regions, warnings = {}, {}, [], [], []
    uncertain = value.get("uncertain_fields", [])
    # Leave invalid types for the normal response validator, rather than coercing them.
    if not isinstance(uncertain, list) or not all(isinstance(key, str) for key in uncertain):
        raise QwenResponseSchemaError(
            "uncertain_fields 必须是字符串数组", detail="uncertain_fields:list_type"
        )
    uncertain = list(uncertain)
    for index, raw in enumerate(facts):
        try:
            fact = MaterialFact.model_validate(raw)
        except ValidationError as exc:
            raise QwenResponseSchemaError("事实结构无效", detail=f"facts.{index}:invalid") from exc
        box = fact.box if valid_normalized_box(fact.box) else []
        if fact.box is not None and fact.box != [] and not box:
            warnings.append(f"facts.{index}.box")
        rows.append(
            {
                "label": fact.label,
                "section": fact.section,
                "value": fact.value
                if isinstance(fact.value, str)
                else json.dumps(fact.value, ensure_ascii=False),
                "uncertain": fact.uncertain,
                "box": box,
            }
        )
        if not fact.field:
            continue
        if fact.uncertain:
            uncertain.append(fact.field)
        if fact.value is not None:
            if fact.field not in fields:
                fields[fact.field] = fact.value
            elif fact.value != fields[fact.field]:
                values = alternatives.setdefault(fact.field, [fields[fact.field]])
                if fact.value not in values:
                    values.append(fact.value)
        if box:
            regions.append({"field": fact.field, "box": box})
    return {
        **{key: value[key] for key in required},
        "fields": fields,
        "raw_fields": rows,
        "evidence_regions": regions,
        "uncertain_fields": list(dict.fromkeys(uncertain)),
        "field_alternatives": alternatives,
        "location_warnings": warnings,
    }


class QwenClient:
    """发送受控视觉模型请求，并把响应转换为经过校验的领域模型。"""

    def __init__(
        self, config: QwenConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """使用指定连接配置和可选测试传输层创建客户端。"""
        self.config = config
        self.transport = transport
        # 一个审核任务会并发发起多次模型请求。按请求创建 AsyncClient 会反复
        # 建立 DashScope 的 TCP/TLS 连接；按事件循环复用连接池，保持 URL 直连
        # 图片的行为不变，只减少传输层的重复握手。
        self._clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = (
            weakref.WeakKeyDictionary()
        )
        self._clients_lock = threading.Lock()

    def _client_for_current_loop(self) -> httpx.AsyncClient:
        """返回当前审核调度事件循环专用的 HTTP 连接池。"""
        loop = asyncio.get_running_loop()
        with self._clients_lock:
            client = self._clients.get(loop)
            if client is None or client.is_closed:
                client = httpx.AsyncClient(
                    timeout=self.config.timeout,
                    transport=self.transport,
                )
                self._clients[loop] = client
            return client

    async def aclose(self) -> None:
        """仅关闭当前循环的连接池；每个循环的所有者须在停止循环前调用。"""
        loop = asyncio.get_running_loop()
        with self._clients_lock:
            client = self._clients.pop(loop, None)
        if client is not None:
            await client.aclose()

    async def extract_document(
        self,
        image: dict[str, Any],
        *,
        retry_reason: str | None = None,
        focus_boxes: list[list[float]] | None = None,
        business: BusinessContext = DEFAULT_BUSINESS,
    ) -> QwenExtraction:
        """Confirm actual material type and read facts in one model request."""
        scope = str(image.get("business_scope", "unknown"))
        content = await self._complete(
            image,
            build_recognition_prompt(
                int(image.get("index", 0)),
                scope,
                business=business,
                retry_reason=retry_reason,
                focus_boxes=focus_boxes,
            ),
            stage="recognition",
        )
        try:
            extraction = parse_qwen_extraction(content)
            policy = business.document_policies.get(extraction.document_type)
            if policy is None:
                return extraction.model_copy(
                    update={
                        "fields": {},
                        "uncertain_fields": [],
                        "evidence_regions": [],
                        "raw_fields": [],
                        "identity_sides": [],
                        "field_alternatives": {},
                    }
                )
            return restrict_extraction_to_policy(extraction, policy, scope)
        except (ValueError, TypeError) as exc:
            cause = (
                QwenResponseSyntaxError("JSON 解码失败")
                if isinstance(exc, json.JSONDecodeError)
                else exc
            )
            raise RuntimeError(f"Qwen 字段响应格式无效：{exc}") from cause

    async def _complete(
        self,
        image: dict[str, Any],
        prompt: str,
        *,
        stage: str = "extraction",
    ) -> str:
        """调用兼容 OpenAI 协议的 Qwen 接口并返回文本内容。"""
        if not self.config.available:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置")
        image_url = image.get("src")
        try:
            parsed = urlparse(image_url) if isinstance(image_url, str) else None
        except ValueError:
            parsed = None
        if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError("模型图片输入必须为 HTTP(S) 地址")
        payload = {
            "model": self.config.model,
            "enable_thinking": False,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt,
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        image_id = image.get("image_id")
        if image_id in (None, ""):
            image_id = image.get("index")
        if image_id is None:
            image_id = "unknown"
        started = perf_counter()
        rate_limited = False
        try:
            client = self._client_for_current_loop()
            response = await client.post(
                f"{self.config.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            if response.status_code == 429:
                # 对短时限流只做一次轻量重试，避免单图调用无限阻塞整批审核。
                rate_limited = True
                await asyncio.sleep(0.25)
                response = await client.post(
                    f"{self.config.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
            response.raise_for_status()
            body = response.json()
            if body["choices"][0].get("finish_reason") == "length":
                self._log_call(
                    stage, image_id, started, rate_limited, body.get("usage"), "truncated"
                )
                raise QwenResponseTruncatedError("模型响应被截断，识别结果不完整")
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content)
            if not isinstance(content, str):
                raise TypeError("message.content 不是字符串")
            self._log_call(stage, image_id, started, rate_limited, body.get("usage"), "ok")
            return content
        except asyncio.CancelledError:
            self._log_call(stage, image_id, started, rate_limited, None, "cancelled")
            raise
        except httpx.HTTPStatusError as exc:
            self._log_call(
                stage,
                image_id,
                started,
                rate_limited,
                None,
                f"http_{exc.response.status_code}",
            )
            detail = exc.response.text[:500].replace("\n", " ")
            raise RuntimeError(f"Qwen 调用失败：HTTP {exc.response.status_code} {detail}") from exc
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            self._log_call(stage, image_id, started, rate_limited, None, type(exc).__name__)
            raise RuntimeError(f"Qwen 调用失败：{exc}") from exc

    def _log_call(
        self,
        stage: str,
        image_id: Any,
        started: float,
        rate_limited: bool,
        usage: Any,
        result: str,
    ) -> None:
        """记录一次模型调用的耗时与用量，失败也记——超时和限流同样要能统计。

        只记标识、阶段、耗时和 token 计数。提示词、响应正文和任何字段值都不进
        这一行：日志文件会被采集、备份、被更多人翻到，身份证号码一旦写进去，
        泄露面比图片走公网大得多。
        """
        counts = usage if isinstance(usage, dict) else {}
        details = counts.get("prompt_tokens_details")
        details = details if isinstance(details, dict) else {}
        logger.info(
            "review qwen call completed stage=%s image_id=%s model=%s"
            " duration_ms=%d prompt_tokens=%d image_tokens=%d"
            " completion_tokens=%d rate_limited=%s result=%s prompt_version=%s",
            stage,
            image_id,
            self.config.model,
            max(0, int((perf_counter() - started) * 1000)),
            _int_or_zero(counts.get("prompt_tokens")),
            _int_or_zero(details.get("image_tokens")),
            _int_or_zero(counts.get("completion_tokens")),
            str(rate_limited).lower(),
            result,
            PROMPT_VERSION,
        )
