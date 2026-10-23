"""Candidate selection, QR decoding and official-page verification as explicit stages."""

import logging
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from forj_review_service.contracts.recognition import (
    RecognitionBatchResult,
    RecognizedDocument,
    RetryAttempt,
)
from forj_review_service.contracts.review import FieldStatus, ImageInput, QrCheck
from forj_review_service.integrations.qr.decoder import QrDecodeResult
from forj_review_service.integrations.qr.web_verifier import QrCodeResult


class QrDecoder(Protocol):
    async def decode(self, image_url: str | None) -> QrDecodeResult: ...


class QrVerifier(Protocol):
    async def verify_with_retry(
        self, raw_value: str, image_index: int | None = None, *, retry_count: int = 1
    ) -> tuple[QrCodeResult, list[RetryAttempt]]: ...


logger = logging.getLogger(__name__)


class QrState(TypedDict, total=False):
    images: list[ImageInput]
    qr_decoder: QrDecoder
    verifier: QrVerifier
    preferred_count: int
    documents: dict[int, RecognizedDocument]
    position: int
    retry_count: int
    attempts: list[RetryAttempt]
    checks: list[QrCheck]
    missing: list[QrCheck]
    codes: list[str]
    decoder: str | None
    decode_result: QrDecodeResult | None
    finished: bool


class QrVerificationWorkflow:
    def __init__(self):
        builder = StateGraph(QrState)
        builder.add_node("select_candidate", self._select)
        builder.add_node("decode", self._decode)
        builder.add_node("verify_official_page", self._verify)
        builder.add_node("advance", self._advance)
        builder.add_edge(START, "select_candidate")
        builder.add_conditional_edges(
            "select_candidate", lambda state: END if state["finished"] else "decode"
        )
        builder.add_conditional_edges(
            "decode", lambda state: "verify_official_page" if state["codes"] else "advance"
        )
        builder.add_edge("verify_official_page", "advance")
        builder.add_edge("advance", "select_candidate")
        self.graph = builder.compile(checkpointer=False)

    @staticmethod
    def _select(state: QrState):
        position = state["position"]
        return {
            "finished": position >= len(state["images"])
            or (
                position >= state["preferred_count"]
                and any(check.domain_valid for check in state["checks"])
            )
        }

    async def _decode(self, state: QrState):
        image = state["images"][state["position"]]
        document = state["documents"].get(image.index)
        codes = []
        decoder = None
        decode_result = None
        # 模型可能把页眉、页脚的打印网址填进 qr_codes，不能作为扫码事实。
        # 原图解码独立于模型提取，即使材料识别失败也继续执行。
        if not image.collection_error:
            decode_result = await state["qr_decoder"].decode(image.recognition_source)
            codes = list(decode_result.values)
            decoder = decode_result.decoder if codes else None
        logger.info(
            "review qr decode image_id=%s source=%s codes=%d error_code=%s duration_ms=%d",
            image.image_id or image.index,
            decoder or (decode_result.decoder if decode_result else "none"),
            len(codes),
            decode_result.error_code
            if decode_result
            else ("collection_error" if image.collection_error else "-"),
            decode_result.elapsed_ms if decode_result else 0,
        )
        if not codes:
            if decode_result is not None:
                if decode_result.error_code == "decode_empty":
                    detail = "ZXing 未读到二维码；OpenCV 也未读到二维码"
                elif decode_result.error_code == "image_too_large":
                    detail = "原图超过二维码读取大小限制，请提供保留二维码清晰度的较小图片"
                elif decode_result.error_code in {
                    "download_timeout",
                    "download_error",
                    "http_status",
                }:
                    detail = f"二维码图片读取失败（{decode_result.error_code}）"
                else:
                    detail = f"二维码读取失败（{decode_result.error_code or 'unknown'}）"
                prefix = "" if document else "材料识别未完成；"
                message = f"{prefix}{detail}，请查看原图人工核验"
            else:
                message = "材料采集失败，尚未取得二维码内容，请重新采集该材料或人工核验"
            missing = QrCheck(
                image_index=image.index,
                status=FieldStatus.REVIEW_REQUIRED,
                message=message,
                decoder=decode_result.decoder if decode_result is not None else "none",
                decode_error=decode_result.error_code if decode_result is not None else None,
                decode_elapsed_ms=decode_result.elapsed_ms if decode_result is not None else None,
            )
            return {"codes": [], "missing": [*state["missing"], missing]}
        return {"codes": codes, "decoder": decoder, "decode_result": decode_result}

    async def _verify(self, state: QrState):
        image = state["images"][state["position"]]
        codes, decoder, decode_result = state["codes"], state["decoder"], state["decode_result"]
        checks = list(state["checks"])
        for code in dict.fromkeys(codes):
            verified, attempts = await state["verifier"].verify_with_retry(
                code, image.index, retry_count=state["retry_count"]
            )
            state["attempts"].extend(attempts)
            status = (
                FieldStatus.MATCH if verified.status == "MATCH" else FieldStatus.REVIEW_REQUIRED
            )
            checks.append(
                QrCheck(
                    image_index=image.index,
                    raw_value=code,
                    url=verified.url,
                    domain_valid=verified.domain_valid,
                    accessible=verified.accessible,
                    page_fields=verified.page_fields,
                    status=status,
                    message=verified.message
                    or (
                        "二维码网页字段已提取" if verified.page_fields else "二维码网页无法完成核验"
                    ),
                    decoder=decoder,
                    decode_error=decode_result.error_code if decode_result is not None else None,
                    decode_elapsed_ms=decode_result.elapsed_ms
                    if decode_result is not None
                    else None,
                )
            )
        return {"checks": checks}

    @staticmethod
    def _advance(state: QrState):
        return {"position": state["position"] + 1}

    async def run(
        self,
        batch: RecognitionBatchResult,
        preferred: list[ImageInput],
        fallback: list[ImageInput],
        retry_count: int,
        *,
        decoder: QrDecoder,
        verifier: QrVerifier,
    ) -> list[QrCheck]:
        images = [*preferred, *fallback]
        result = await self.graph.ainvoke(
            {
                "qr_decoder": decoder,
                "verifier": verifier,
                "images": images,
                "preferred_count": len(preferred),
                "documents": {
                    document.image_index: document for document in batch.recognized_documents
                },
                "position": 0,
                "retry_count": retry_count,
                "attempts": batch.retry_summary.attempts,
                "checks": [],
                "missing": [],
            },
            config={"recursion_limit": max(25, 5 * len(images) + 5)},
        )
        checks = result["checks"]
        if any(check.domain_valid for check in checks):
            return checks
        # 其他候选图中的无关二维码不能遮住回收证明的读取故障。
        preferred_indices = {image.index for image in preferred}
        failures = [
            check
            for check in result["missing"]
            if check.image_index in preferred_indices
            or check.decode_error not in {None, "decode_empty"}
        ]
        return [*failures, *checks] or result["missing"][:1]
