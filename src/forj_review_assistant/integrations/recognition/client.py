"""Qwen 文档识别客户端。

主要职责：发送受控模型请求并校验、过滤结构化提取结果。
修改日期：2026-08-26
"""

import asyncio
import json
import logging
import re
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from forj_review_assistant.businesses.materials import (
    DocumentPolicy,
    build_classification_prompt,
    build_unknown_extraction_prompt,
)
from forj_review_assistant.config.settings import QwenConfig
from forj_review_assistant.contracts.recognition import (
    QwenClassification,
    QwenExtraction,
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


FIELD_LABEL_ECHOES: dict[str, frozenset[str]] = {
    "old_vehicle.recycle_date": frozenset({"交车日期", "报废交车日期"}),
    "scrap_certificate.certificate_no": frozenset({"回收证明编号", "报废证明编号"}),
    "invoice.invoice_no": frozenset({"发票号码", "数电号码"}),
    "invoice.amount": frozenset({"价税合计", "价税合计小写", "价税合计（小写）", "开票金额"}),
    "invoice.invoice_date": frozenset({"开票日期"}),
    "new_vehicle.origin": frozenset({"产地"}),
    "invoice.terminal_certificate_no": frozenset({"统一社会信用代码", "纳税人识别号", "终端证件号"}),
    "invoice.phone": frozenset({"电话", "终端客户手机号"}),
    "vehicle.type": frozenset({"车辆类型"}),
    "vehicle.vin": frozenset({"车辆识别代号", "车辆识别代号/车架号码", "车架号码"}),
    "vehicle.plate_no": frozenset({"号牌号码", "车牌号"}),
    "vehicle.owner": frozenset({"所有人", "机动车所有人", "购买方名称"}),
    "vehicle.engine_model": frozenset({"发动机型号"}),
    "vehicle.fuel_type": frozenset({"燃料种类", "燃料类型"}),
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
    normalized["fields"] = {
        field: value
        for field, value in fields.items()
        if not (
            isinstance(value, str)
            and (
                value.strip() == field
                or value.strip() in FIELD_LABEL_ECHOES.get(field, frozenset())
            )
        )
    }


def _restrict_extraction_to_policy(
    extraction: QwenExtraction,
    policy: DocumentPolicy,
    business_scope: str,
) -> QwenExtraction:
    """只保留当前材料和业务范围白名单中的字段及证据位置。"""
    allowed = set(policy.fields_for_scope(business_scope))
    fields = {
        field: value
        for field, value in extraction.fields.items()
        if field in allowed
    }
    uncertain_fields = list(
        dict.fromkeys(
            field for field in extraction.uncertain_fields if field in allowed
        )
    )
    evidence_regions = [
        region for region in extraction.evidence_regions if region.field in allowed
    ]
    return extraction.model_copy(
        update={
            "fields": fields,
            "uncertain_fields": uncertain_fields,
            "evidence_regions": evidence_regions,
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
    normalized = dict(value)
    fields = normalized.get("fields")
    if fields is None:
        normalized["fields"] = {}
    elif isinstance(fields, dict):
        normalized["fields"] = {
            field: field_value
            for field, field_value in fields.items()
            if field_value is not None
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


class QwenClient:
    """发送受控视觉模型请求，并把响应转换为经过校验的领域模型。"""

    def __init__(self, config: QwenConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """使用指定连接配置和可选测试传输层创建客户端。"""
        self.config = config
        self.transport = transport

    async def classify_document(self, image: dict[str, Any]) -> QwenClassification:
        """仅判断未知图片的材料类型，不提取业务字段。"""
        content = await self._complete(
            image,
            build_classification_prompt(),
            stage="classification",
        )
        try:
            return QwenClassification.model_validate_json(content)
        except ValueError as exc:
            raise RuntimeError(f"Qwen 分类响应格式无效：{exc}") from exc

    async def extract_fields(
        self,
        image: dict[str, Any],
        policy: DocumentPolicy,
        *,
        retry_reason: str | None = None,
    ) -> QwenExtraction:
        """按已知材料策略提取白名单字段。"""
        image_index = int(image.get("index", 0))
        business_scope = str(image.get("business_scope", "unknown"))
        content = await self._complete(
            image,
            policy.build_extraction_prompt(
                image_index,
                business_scope,
                retry_reason=retry_reason,
            ),
            stage="extraction",
        )
        try:
            extraction = parse_qwen_extraction(content)
            return _restrict_extraction_to_policy(
                extraction,
                policy,
                business_scope,
            )
        except (ValueError, TypeError) as exc:
            if isinstance(exc, json.JSONDecodeError):
                cause: BaseException = QwenResponseSyntaxError("JSON 解码失败")
            else:
                cause = exc
            raise RuntimeError(f"Qwen 字段响应格式无效：{exc}") from cause

    async def extract_unknown(
        self,
        image: dict[str, Any],
        *,
        retry_reason: str | None = None,
    ) -> QwenExtraction:
        """在一次模型调用中完成未知材料分类和受限字段提取。"""
        image_index = int(image.get("index", 0))
        business_scope = str(image.get("business_scope", "unknown"))
        content = await self._complete(
            image,
            build_unknown_extraction_prompt(
                image_index,
                business_scope,
                retry_reason=retry_reason,
            ),
            stage="unknown_extraction",
        )
        try:
            return parse_qwen_extraction(content)
        except (ValueError, TypeError) as exc:
            if isinstance(exc, json.JSONDecodeError):
                cause: BaseException = QwenResponseSyntaxError("JSON 解码失败")
            else:
                cause = exc
            raise RuntimeError(f"Qwen 未分类资料响应格式无效：{exc}") from cause

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
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt + (f"\n当前页面上传槽位：组内第{image['group_order']}张。只提取适用于当前槽位的字段。" if image.get("group_order") is not None else "")},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
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
            async with httpx.AsyncClient(timeout=self.config.timeout, transport=self.transport) as client:
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
            raise RuntimeError(
                f"Qwen 调用失败：HTTP {exc.response.status_code} {detail}"
            ) from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
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
            " completion_tokens=%d rate_limited=%s result=%s",
            stage,
            image_id,
            self.config.model,
            max(0, int((perf_counter() - started) * 1000)),
            _int_or_zero(counts.get("prompt_tokens")),
            _int_or_zero(details.get("image_tokens")),
            _int_or_zero(counts.get("completion_tokens")),
            str(rate_limited).lower(),
            result,
        )
