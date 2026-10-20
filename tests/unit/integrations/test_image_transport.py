import httpx
import pytest

from forj_review_service.integrations.qr.decoder import QrImageDecoder
from forj_review_service.integrations.qr.image_transport import ImageTransport


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [
    ["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["::1"],
    ["::ffff:127.0.0.1"], ["93.184.216.34", "192.168.0.1"],
])
async def test_internal_and_mixed_dns_results_are_rejected_before_connecting(addresses):
    async def resolver(host, port):
        return addresses

    def forbidden(request):
        pytest.fail("a rejected image address must never reach the network")

    transport = ImageTransport(resolver=resolver, transport=httpx.MockTransport(forbidden))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        with pytest.raises(ValueError, match="内部网络"):
            await client.get("https://images.example.test/proof.jpg")


@pytest.mark.asyncio
async def test_public_dns_is_pinned_and_preserves_original_tls_and_host_identity():
    calls = []

    async def resolver(host, port):
        assert (host, port) == ("images.example.test", 443)
        return ["93.184.216.34"]

    def receive(request):
        calls.append(request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "images.example.test"
        assert request.extensions["sni_hostname"] == "images.example.test"
        return httpx.Response(200, content=b"synthetic")

    transport = ImageTransport(resolver=resolver, transport=httpx.MockTransport(receive))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        response = await client.get("https://images.example.test/proof.jpg?signature=synthetic")
        assert response.url.host == "images.example.test"
        assert response.content == b"synthetic"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_redirect_to_metadata_service_is_rechecked_and_blocked():
    calls = []

    async def resolver(host, port):
        return ["93.184.216.34"] if host == "images.example.test" else ["169.254.169.254"]

    def receive(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    decoder = QrImageDecoder(transport=ImageTransport(resolver=resolver, transport=httpx.MockTransport(receive)))
    result = await decoder.decode("https://images.example.test/proof.jpg")
    assert result.error_code == "image_rejected"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_internal_images_require_an_exact_operator_configured_host():
    async def resolver(host, port):
        return ["192.168.1.2"]

    transport = ImageTransport(private_hosts=("images.internal",), resolver=resolver,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"image")))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        assert (await client.get("https://images.internal/proof.jpg")).status_code == 200
        with pytest.raises(ValueError):
            await client.get("https://other.images.internal/proof.jpg")


@pytest.mark.asyncio
async def test_malformed_authority_and_embedded_credentials_are_rejected():
    decoder = QrImageDecoder()
    for url in ("http://user:secret@example.test/a", "https://example.test:invalid/a", "file:///etc/passwd"):
        assert (await decoder.decode(url)).error_code == "invalid_url"
