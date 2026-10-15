import ssl

import httpx
import pytest

import app.services.qr as qr_module
from app.models.review import FieldObservation, FieldStatus, ImageInput, ReviewRequest
from app.services.qr import (
    QrCodeResult,
    QrWebVerifier,
    canonicalize_official_qr_url,
    create_official_ssl_context,
    extract_page_fields,
    validate_qr_url,
)
from app.services.review import ReviewService
from app.workflow.models import AgentBatchResult, RecognizedDocument


def test_qr_url_requires_exact_official_https_host():
    assert validate_qr_url("https://qclt.mofcom.gov.cn/deal/scrap/validdata/x", ["qclt.mofcom.gov.cn"])
    assert not validate_qr_url("http://qclt.mofcom.gov.cn/deal/x", ["qclt.mofcom.gov.cn"])
    assert not validate_qr_url("https://evil.qclt.mofcom.gov.cn/deal/x", ["qclt.mofcom.gov.cn"])


def test_official_http_qr_is_upgraded_to_https_before_access():
    assert canonicalize_official_qr_url(
        "http://qclt.mofcom.gov.cn/deal/scrap/validdata/token",
        ["qclt.mofcom.gov.cn"],
    ) == "https://qclt.mofcom.gov.cn/deal/scrap/validdata/token"
    assert canonicalize_official_qr_url("http://evil.example/x", ["qclt.mofcom.gov.cn"]) is None


def test_official_ssl_context_keeps_certificate_verification_enabled():
    context = create_official_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_qr_result_preserves_existing_positional_constructor_order():
    result = QrCodeResult(
        1,
        "raw-value",
        "https://qclt.mofcom.gov.cn/deal/1",
        True,
        {"vin": "VIN-1"},
        "MATCH",
    )

    assert result.page_fields == {"vin": "VIN-1"}
    assert result.status == "MATCH"
    assert result.accessible is None


def test_extract_recycling_certificate_number_and_vin_from_html():
    html = """
    <table><tr><th>回收证明编号</th><td>回收-2026-001</td></tr>
    <tr><th>车架号</th><td>LSVAA123456789012</td></tr></table>
    """
    assert extract_page_fields(html) == {
        "certificate_no": "回收-2026-001",
        "vin": "LSVAA123456789012",
    }


def test_extract_recycling_certificate_fields_from_embedded_json_and_common_aliases():
    html = '''
    <script type="application/json">
      {"certificateNo":"回收-2026-002","vehicleIdentificationNo":"LSVBB123456789012"}
    </script>
    '''
    assert extract_page_fields(html) == {
        "certificate_no": "回收-2026-002",
        "vin": "LSVBB123456789012",
    }


def test_extract_recycling_certificate_fields_from_equals_and_vehicle_identification_code():
    html = "<div>报废证明编号 = A-2026-003</div><div>车辆识别码 = LSVCC123456789012</div>"
    assert extract_page_fields(html) == {
        "certificate_no": "A-2026-003",
        "vin": "LSVCC123456789012",
    }


@pytest.mark.asyncio
async def test_verifier_rejects_redirect_to_untrusted_host():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    verifier = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        transport=httpx.MockTransport(handler),
    )
    result = await verifier.verify("https://qclt.mofcom.gov.cn/start")
    assert result.domain_valid is True
    assert result.accessible is False
    assert result.status == "REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_verifier_does_not_access_an_untrusted_qr_url():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    verifier = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        transport=httpx.MockTransport(handler),
    )

    result = await verifier.verify("https://evil.example/deal/1")

    assert result.domain_valid is False
    assert result.accessible is None
    assert result.status == "REVIEW_REQUIRED"
    assert requests == []


@pytest.mark.asyncio
async def test_web_retry_reuses_one_http_client(monkeypatch):
    responses = iter([
        httpx.Response(503),
        httpx.Response(
            200,
            text="回收证明编号：A1 车架号：LSVAA123456789012",
        ),
    ])
    transport = httpx.MockTransport(lambda request: next(responses))
    real_async_client = httpx.AsyncClient
    client_count = 0

    def recording_client(*args, **kwargs):
        nonlocal client_count
        client_count += 1
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(qr_module.httpx, "AsyncClient", recording_client)
    verifier = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        transport=transport,
    )

    result, attempts = await verifier.verify_with_retry(
        "https://qclt.mofcom.gov.cn/deal/1",
        retry_count=1,
    )

    assert result.status == "MATCH"
    assert len(attempts) == 2
    assert client_count == 1


@pytest.mark.asyncio
async def test_review_keeps_an_untrusted_qr_for_manual_review():
    service = ReviewService()
    request = ReviewRequest(
        page_url="https://example.test",
        region="qingdao",
        images=[
            ImageInput(
                index=1,
                src="https://images.example.test/synthetic.jpg",
                categoryHint="scrap_certificate",
                businessScope="old_vehicle",
            )
        ],
    )

    checks = await service._collect_qr_checks(request, decoded_batch(service, 1, "https://evil.example/deal/1"))

    assert len(checks) == 1
    assert checks[0].domain_valid is False
    assert checks[0].accessible is None
    assert checks[0].status.value == "REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_review_scans_image_when_agent_identifies_scrap_certificate():
    service = ReviewService()
    service.qr_web = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200,
            text="回收证明编号：A1 车架号：LSVAA123456789012",
        )),
    )
    request = ReviewRequest(
        page_url="https://example.test",
        region="qingdao",
        images=[ImageInput(index=1, src="https://images.example.test/synthetic.jpg", businessScope="old_vehicle")],
    )
    batch = AgentBatchResult(observations=[FieldObservation(
        field="vehicle.vin", source_type="image", source_id="1", image_index=1, value="LSVAA123456789012",
        document_type="scrap_certificate",
    )])
    batch.recognized_documents = decoded_batch(service, 1, "http://qclt.mofcom.gov.cn:80/x").recognized_documents
    checks = await service._collect_qr_checks(request, batch)
    assert len(checks) == 1
    assert checks[0].url == "https://qclt.mofcom.gov.cn/x"
    assert checks[0].page_fields["vin"] == "LSVAA123456789012"


@pytest.mark.asyncio
async def test_one_decoded_qr_does_not_create_missing_results_for_other_old_images():
    """Only the image containing the certificate QR is an external check target.

    Vehicle-license and registration images in the old-vehicle group do not
    carry the recycling-certificate QR. Their decode misses must not create
    additional REVIEW_REQUIRED cards once one QR has been found.
    """

    service = ReviewService()
    service.qr_web = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="回收证明编号：A1 车架号：LSVAA123456789012",
            )
        ),
    )
    request = ReviewRequest(
        page_url="https://example.test",
        region="qingdao",
        images=[
            ImageInput(
                index=1,
                src="https://images.example.test/synthetic.jpg",
                categoryHint="vehicle_license",
                businessScope="old_vehicle",
            ),
            ImageInput(
                index=2,
                src="https://images.example.test/synthetic.jpg",
                categoryHint="registration_certificate",
                businessScope="old_vehicle",
            ),
            ImageInput(
                index=3,
                src="https://images.example.test/synthetic.jpg",
                categoryHint="scrap_certificate",
                businessScope="old_vehicle",
            ),
        ],
    )

    checks = await service._collect_qr_checks(request, decoded_batch(service, 3, "http://qclt.mofcom.gov.cn:80/x"))

    assert len(checks) == 1
    assert checks[0].image_index == 3
    assert checks[0].status is FieldStatus.MATCH


@pytest.mark.asyncio
async def test_qr_decodes_original_for_unknown_scope_with_scrap_hint():
    service = ReviewService()
    service.qr_web = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="回收证明编号：A1 车架号：LSVAA123456789012",
            )
        ),
    )
    request = ReviewRequest(
        page_url="https://example.test",
        region="qingdao",
        images=[
            ImageInput(
                index=4,
                src="https://images.example.test/synthetic.jpg",
                categoryHint="scrap_certificate",
                businessScope="unknown",
            )
        ],
    )

    checks = await service._collect_qr_checks(request, decoded_batch(service, 4, "http://qclt.mofcom.gov.cn:80/x"))

    assert len(checks) == 1
    assert checks[0].status is FieldStatus.MATCH


def decoded_batch(service, index, url):
    return AgentBatchResult(recognized_documents=[RecognizedDocument(
        target_id=str(index), image_index=index, document_type="scrap_certificate", qr_codes=[url],
    )])
