"""Provider throttling is visible and never burns the remaining turn budget on retries."""

import httpx
import pytest

from forj_review_config_service.config.settings import Settings
from forj_review_config_service.contracts.models import ConfigurationError
from forj_review_config_service.integrations.conversation.client import ConversationClient


@pytest.mark.asyncio
async def test_rate_limit_is_distinct_from_connection_failure_and_does_not_retry():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "private provider payload"}})

    model = ConversationClient(Settings(api_key="synthetic"))
    await model.client.aclose()
    model.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ConfigurationError) as exc:
            await model.complete([{"role": "user", "content": "check"}], [])
        assert exc.value.code == "MODEL_RATE_LIMITED"
        assert "限流" in exc.value.message
        assert "private" not in exc.value.message and len(calls) == 1
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,issue,finish_reason",
    [
        ("private-phone invalid json", "invalid_json", "length"),
        ('{"private": "private-phone"}', "invalid_observations", "length"),
        (
            '{"observations": [{"sample_id": "invented", "value": "private-phone"}]}',
            "invalid_observations",
            "length",
        ),
        ('{"observations": [{"sample_id": {}}]}', "invalid_observations", {}),
    ],
)
async def test_material_format_failure_has_safe_diagnostic_metadata(content, issue, finish_reason):
    model = ConversationClient(Settings(api_key="synthetic"))

    async def complete(messages, tools):
        return {"content": content, "_finish_reason": finish_reason}

    model.complete = complete
    try:
        with pytest.raises(ConfigurationError) as error:
            await model.analyze(
                [{"sample_id": "sample", "url": "https://example.test/image"}], "电话归属"
            )
        assert error.value.code == "MODEL_FORMAT"
        assert error.value.diagnostic == {
            "response_chars": len(content),
            "issue": issue,
            "finish_reason": "length" if finish_reason == "length" else "unknown",
        }
        assert "private-phone" not in str(error.value) + str(error.value.diagnostic)
    finally:
        await model.close()
