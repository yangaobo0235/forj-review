import ssl

import httpx
import pytest

import forj_review_assistant.integrations.qr.web_verifier as qr_module
from forj_review_assistant.contracts.recognition import (
    AgentBatchResult,
    RecognizedDocument,
)
from forj_review_assistant.contracts.review import (
    FieldObservation,
    FieldStatus,
    ImageInput,
    ReviewRequest,
)
from forj_review_assistant.integrations.qr.web_verifier import (
    QrCodeResult,
    QrWebVerifier,
    canonicalize_official_qr_url,
    create_official_ssl_context,
    extract_page_fields,
    validate_qr_url,
)
from forj_review_assistant.runtime.review_service import ReviewService


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
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED
    compatibility = create_official_ssl_context(tls12_only=True)
    assert compatibility.minimum_version == ssl.TLSVersion.TLSv1_2
    assert compatibility.maximum_version == ssl.TLSVersion.TLSv1_2
    assert compatibility.verify_mode == ssl.CERT_REQUIRED
    assert compatibility.check_hostname is True


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
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_transient_connection_failures_retry_and_keep_safe_diagnostics(error, caplog, monkeypatch):
    calls = 0
    contexts = []
    real_async_client = httpx.AsyncClient

    def recording_client(*args, **kwargs):
        contexts.append(kwargs["verify"])
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(qr_module.httpx, "AsyncClient", recording_client)

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error("sensitive-token-must-not-be-logged", request=request)
        return httpx.Response(200, text="回收证明编号：A1 车架号：LSVAA123456789012")

    verifier = QrWebVerifier(allowed_hosts=["qclt.mofcom.gov.cn"], transport=httpx.MockTransport(handler))
    with caplog.at_level("INFO", logger="forj_review_assistant"):
        result, attempts = await verifier.verify_with_retry("https://qclt.mofcom.gov.cn/sensitive-token", 4)
    assert result.status == "MATCH"
    assert calls == len(attempts) == 2
    assert result.http_status == 200
    assert attempts[0].reason_code == ("timeout" if error is httpx.ReadTimeout else "connection_error")
    assert attempts[1].reason_code == "completed"
    assert attempts[1].strategy == ("official_page" if error is httpx.ReadTimeout else "official_page_tls12")
    assert len(contexts) == (1 if error is httpx.ReadTimeout else 2)
    if len(contexts) == 2:
        assert contexts[-1].maximum_version == ssl.TLSVersion.TLSv1_2
        assert contexts[-1].verify_mode == ssl.CERT_REQUIRED
        assert contexts[-1].check_hostname is True
    assert "sensitive-token" not in caplog.text
    assert "host=qclt.mofcom.gov.cn" in caplog.text
    assert "attempt=2" in caplog.text


@pytest.mark.asyncio
async def test_network_failure_has_bounded_retries_and_actionable_message():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("network failed", request=request)

    verifier = QrWebVerifier(allowed_hosts=["qclt.mofcom.gov.cn"], transport=httpx.MockTransport(handler))
    result, attempts = await verifier.verify_with_retry("https://qclt.mofcom.gov.cn/proof", retry_count=99)
    assert calls == len(attempts) == 2
    assert result.status == "REVIEW_REQUIRED"
    assert result.accessible is False
    assert result.error_code == "connection_error"
    assert "连接官网失败" in result.message


@pytest.mark.asyncio
async def test_certificate_failure_is_not_retried_or_hidden():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("certificate failed", request=request) from ssl.SSLCertVerificationError("bad certificate")

    verifier = QrWebVerifier(allowed_hosts=["qclt.mofcom.gov.cn"], transport=httpx.MockTransport(handler))
    result, attempts = await verifier.verify_with_retry("https://qclt.mofcom.gov.cn/proof")
    assert calls == len(attempts) == 1
    assert result.error_code == "tls_certificate_error"
    assert result.retryable is False
    assert "证书校验失败" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 503])
async def test_http_status_failures_preserve_reason_and_retry_policy(status):
    verifier = QrWebVerifier(allowed_hosts=["qclt.mofcom.gov.cn"],
                             transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    result, attempts = await verifier.verify_with_retry("https://qclt.mofcom.gov.cn/proof")
    assert result.http_status == status
    assert result.error_code == "http_status"
    assert str(status) in result.message
    assert len(attempts) == (2 if status in {429, 503} else 1)


@pytest.mark.asyncio
async def test_accessible_page_with_missing_fields_does_not_claim_extraction_success():
    service = ReviewService()
    service.qr_web = QrWebVerifier(allowed_hosts=["qclt.mofcom.gov.cn"],
                                 transport=httpx.MockTransport(lambda request: httpx.Response(200, text="回收证明编号：A1")))
    request = ReviewRequest(page_url="https://example.test", region="qingdao", images=[
        ImageInput(index=1, src="https://images.example.test/synthetic.jpg", categoryHint="scrap_certificate", businessScope="old_vehicle")
    ])
    batch = decoded_batch(service, 1, "https://qclt.mofcom.gov.cn/proof")
    checks = await service._collect_qr_checks(request, batch)
    assert checks[0].accessible is True
    assert checks[0].status == FieldStatus.REVIEW_REQUIRED
    assert "未提取到完整" in checks[0].message
    assert batch.retry_summary.attempts[0].reason_code == "fields_missing"


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
