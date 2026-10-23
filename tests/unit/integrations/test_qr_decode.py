import struct
import zlib

import cv2
import httpx
import numpy as np
import pytest
import zxingcpp

from forj_review_service.contracts.recognition import (
    RecognitionBatchResult,
    RecognizedDocument,
)
from forj_review_service.contracts.review import FieldStatus, ImageInput, ReviewRequest
from forj_review_service.integrations.qr.decoder import QrDecodeResult, QrImageDecoder
from forj_review_service.integrations.qr.web_verifier import QrWebVerifier
from forj_review_service.integrations.qr.workflow import QrVerificationWorkflow
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import business_registry


def qr_png(value: str) -> bytes:
    image = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, value, 300, 300)
    ok, encoded = cv2.imencode(".png", np.asarray(image))
    assert ok
    return bytes(encoded)


@pytest.mark.asyncio
async def test_qr_decoder_rejects_large_pixel_header_before_native_allocation(monkeypatch):
    # 只修改 PNG 头部，不在测试中分配大图。
    payload = bytearray(qr_png("synthetic"))
    payload[16:24] = struct.pack(">II", 6000, 6000)
    payload[29:33] = struct.pack(">I", zlib.crc32(payload[12:29]))

    def native_decode(*args):
        pytest.fail("尺寸校验必须先于原生图片内存分配")

    monkeypatch.setattr(cv2, "imdecode", native_decode)
    result = await QrImageDecoder(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=bytes(payload)))
    ).decode("https://images.example.test/oversize.png")
    assert result.error_code == "decoder_error"


@pytest.mark.asyncio
async def test_qr_decoder_contains_native_decode_errors(monkeypatch):
    payload = qr_png("synthetic")

    def native_decode(*args):
        raise cv2.error("synthetic native failure")

    monkeypatch.setattr(cv2, "imdecode", native_decode)
    result = await QrImageDecoder(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    ).decode("https://images.example.test/broken.png")
    assert result.error_code == "decoder_error"


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

    monkeypatch.setattr(zxingcpp, "read_barcodes", lambda image, **kwargs: [])
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

    def broken_reader(image, **kwargs):
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
@pytest.mark.parametrize("has_document", [False, True])
async def test_review_decodes_original_independently_of_material_extraction(has_document):
    class Decoder:
        async def decode(self, image_url):
            return QrDecodeResult(
                values=("https://qclt.mofcom.gov.cn/proof/local",), decoder="zxing"
            )

    service = ReviewService(qr_decoder=Decoder(), registry=business_registry())
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
    batch = RecognitionBatchResult(
        total_count=1,
        recognized_documents=[
            RecognizedDocument(
                target_id="0", image_index=0, document_type="scrap_certificate", qr_codes=[]
            )
        ]
        if has_document
        else [],
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


@pytest.mark.parametrize("with_qr", [False, True])
def test_numeric_barcode_is_never_returned_as_qr_content(with_qr):
    value = "https://qclt.mofcom.gov.cn/deal/scrap/validdata/synthetic"
    canvas = np.full((1100, 1500), 255, dtype=np.uint8)
    barcode = np.asarray(
        zxingcpp.write_barcode(zxingcpp.BarcodeFormat.Code128, "12345678901234", 900, 220)
    )
    height, width = barcode.shape
    canvas[20 : 20 + height, 20 : 20 + width] = barcode
    if with_qr:
        qr = cv2.imdecode(np.frombuffer(qr_png(value), np.uint8), cv2.IMREAD_GRAYSCALE)
        height, width = qr.shape
        canvas[500 : 500 + height, 20 : 20 + width] = qr
    ok, encoded = cv2.imencode(".png", canvas)
    assert ok
    result = QrImageDecoder._decode_bytes(bytes(encoded))
    if with_qr:
        assert result == ((value,), "zxing")
    else:
        assert result is None


def test_green_rotated_certificate_qr_preserves_complete_payload():
    value = "http://qclt.mofcom.gov.cn:80/deal/scrap/validdata/" + "Ab3+/xY=" * 24
    qr = np.asarray(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.QRCode, value, 600, 600))
    image = np.zeros((*qr.shape, 3), dtype=np.uint8)
    image[qr > 127] = (190, 220, 195)
    image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    assert QrImageDecoder._decode_bytes(bytes(encoded)) == ((value,), "zxing")


@pytest.mark.asyncio
async def test_qr_decoder_reads_image_above_former_eight_mb_limit():
    value = "https://qclt.mofcom.gov.cn/deal/scrap/validdata/large"
    payload = qr_png(value).ljust(8_000_001, b"\0")
    result = await QrImageDecoder(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    ).decode("https://images.example.test/large.png")
    assert result.values == (value,)
    assert result.error_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_length", [False, True])
async def test_qr_decoder_enforces_size_limit_before_native_decode(declared_length, monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(3):
                yield b"x" * 16

    def handler(request):
        return httpx.Response(
            200, headers={"content-length": "48"} if declared_length else {}, stream=Stream()
        )

    def native_decode(content):
        pytest.fail("Oversized image must be rejected before decoding")

    monkeypatch.setattr(QrImageDecoder, "_decode_bytes", staticmethod(native_decode))
    result = await QrImageDecoder(max_bytes=32, transport=httpx.MockTransport(handler)).decode(
        "https://images.example.test/oversize.png"
    )
    assert result.values == ()
    assert result.error_code == "image_too_large"


@pytest.mark.asyncio
@pytest.mark.parametrize("decode_error", [None, "decode_empty", "download_error"])
async def test_model_printed_url_never_substitutes_for_decoded_qr(decode_error):
    printed_url = "https://qclt.mofcom.gov.cn/deal/scrap/printPreview/synthetic"
    qr_url = "https://qclt.mofcom.gov.cn/deal/scrap/validdata/synthetic"
    decoded_images = []
    official_requests = []

    class Decoder:
        async def decode(self, image_url):
            decoded_images.append(image_url)
            return QrDecodeResult(
                values=() if decode_error else (qr_url,),
                decoder="none" if decode_error else "zxing",
                error_code=decode_error,
            )

    def official_handler(request):
        official_requests.append(str(request.url))
        return httpx.Response(200, text="回收证明编号：SYNTHETIC 车架号：VINLOCAL")

    service = ReviewService(qr_decoder=Decoder(), registry=business_registry())
    service.qr_web = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"], transport=httpx.MockTransport(official_handler)
    )
    image = ImageInput(
        index=0,
        src="https://images.example.test/certificate.png",
        category_hint="scrap_certificate",
        business_scope="old_vehicle",
    )
    batch = RecognitionBatchResult(
        recognized_documents=[
            RecognizedDocument(
                target_id="0",
                image_index=0,
                document_type="scrap_certificate",
                qr_codes=[printed_url],
            )
        ]
    )
    checks = await service._collect_qr_checks(
        ReviewRequest(page_url="https://example.test/review", region="qingdao", images=[image]),
        batch,
    )
    assert decoded_images == [image.src]
    assert len(checks) == 1
    if decode_error:
        assert checks[0].status is FieldStatus.REVIEW_REQUIRED
        assert checks[0].raw_value is None
        assert checks[0].decode_error == decode_error
        assert official_requests == []
    else:
        assert checks[0].status is FieldStatus.MATCH
        assert checks[0].raw_value == qr_url
        assert checks[0].decoder == "zxing"
        assert official_requests == [qr_url]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["image_too_large", "download_error", "decode_empty"])
async def test_unrelated_fallback_qr_cannot_hide_certificate_failure(error):
    certificate = ImageInput(index=0, src="https://images.example.test/certificate.png")
    other = ImageInput(index=1, src="https://images.example.test/other.png")

    class Decoder:
        async def decode(self, image_url):
            if image_url == certificate.src:
                return QrDecodeResult(error_code=error)
            return QrDecodeResult(values=("12345678901234",), decoder="zxing")

    checks = await QrVerificationWorkflow().run(
        RecognitionBatchResult(),
        [certificate],
        [other],
        0,
        decoder=Decoder(),
        verifier=QrWebVerifier(allowed_hosts=["qclt.mofcom.gov.cn"]),
    )
    assert len(checks) == 2
    assert checks[0].image_index == 0
    assert checks[0].decode_error == error
    assert checks[0].status is FieldStatus.REVIEW_REQUIRED
    assert checks[1].raw_value == "12345678901234"
    assert checks[1].domain_valid is False
    if error == "image_too_large":
        assert "超过二维码读取大小限制" in checks[0].message
