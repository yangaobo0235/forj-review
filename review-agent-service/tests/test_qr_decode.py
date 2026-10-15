import cv2
import httpx
import numpy as np
import pytest
import zxingcpp

from app.models.review import ImageInput, ReviewRequest
from app.services.qr import QrWebVerifier
from app.services.qr_decode import QrImageDecoder
from app.services.review import ReviewService
from app.workflow.models import AgentBatchResult, RecognizedDocument


def qr_png(value: str) -> bytes:
    image = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, value, 300, 300)
    ok, encoded = cv2.imencode(".png", np.asarray(image))
    assert ok
    return bytes(encoded)


@pytest.mark.asyncio
async def test_qr_decoder_reads_url_in_memory_with_zxing_first():
    payload = qr_png("https://qclt.mofcom.gov.cn/proof/1")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "image/png"})

    result = await QrImageDecoder(transport=httpx.MockTransport(handler)).decode(
        "https://images.example.test/certificate.png"
    )

    assert result.values == ("https://qclt.mofcom.gov.cn/proof/1",)
    assert result.decoder == "zxing"
    assert result.error_code is None


@pytest.mark.asyncio
async def test_qr_decoder_uses_opencv_when_zxing_has_no_result(monkeypatch):
    payload = qr_png("https://qclt.mofcom.gov.cn/proof/2")
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    monkeypatch.setattr(zxingcpp, "read_barcodes", lambda image: [])
    result = await QrImageDecoder(transport=httpx.MockTransport(handler)).decode(
        "https://images.example.test/certificate.png"
    )

    assert result.values == ("https://qclt.mofcom.gov.cn/proof/2",)
    assert result.decoder == "opencv"


@pytest.mark.asyncio
async def test_qr_decoder_uses_opencv_when_zxing_raises(monkeypatch):
    payload = qr_png("https://qclt.mofcom.gov.cn/proof/3")
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    def broken_reader(image):
        raise RuntimeError("native ZXing failure")

    monkeypatch.setattr(zxingcpp, "read_barcodes", broken_reader)
    result = await QrImageDecoder(transport=httpx.MockTransport(handler)).decode(
        "https://images.example.test/certificate.png"
    )

    assert result.values == ("https://qclt.mofcom.gov.cn/proof/3",)
    assert result.decoder == "opencv"


@pytest.mark.asyncio
async def test_qr_decoder_reports_download_failure_without_retry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    result = await QrImageDecoder(transport=httpx.MockTransport(handler)).decode(
        "https://images.example.test/certificate.png"
    )

    assert result.values == ()
    assert result.error_code == "http_status"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_review_uses_local_decoder_when_model_document_has_no_qr_code():
    class Decoder:
        async def decode(self, image_url):
            from app.services.qr_decode import QrDecodeResult
            return QrDecodeResult(values=("https://qclt.mofcom.gov.cn/proof/local",), decoder="zxing")

    service = ReviewService(qr_decoder=Decoder())
    service.qr_web = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="回收证明编号：LOCAL 车架号：VINLOCAL")
        ),
    )
    image = ImageInput(
        index=0,
        src="https://images.example.test/certificate.png",
        category_hint="scrap_certificate",
        business_scope="old_vehicle",
    )
    batch = AgentBatchResult(
        total_count=1,
        recognized_documents=[
            RecognizedDocument(
                target_id="0", image_index=0, document_type="scrap_certificate", qr_codes=[]
            )
        ],
    )

    checks = await service._collect_qr_checks(
        ReviewRequest(
            page_url="https://example.test/review",
            region="qingdao",
            images=[image],
        ),
        batch,
    )

    assert checks[0].raw_value == "https://qclt.mofcom.gov.cn/proof/local"
    assert checks[0].decoder == "zxing"
