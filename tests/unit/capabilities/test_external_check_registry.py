import pytest

from forj_review_service.capabilities.external_checks import (
    ExternalCheckRegistry,
    UnknownExternalCheck,
)
from forj_review_service.capabilities.specs import (
    CapabilitySpec,
    ReviewExecutionContext,
)
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import ReviewRequest
from tests.support.pages import TRANSFER_DEFAULT


def context():
    return ReviewExecutionContext(
        request=ReviewRequest(
            page_url="https://example.test/transfer",
            business_type="transfer",
            region="default",
        ),
        profile=TRANSFER_DEFAULT,
        batch=RecognitionBatchResult(),
        observations=(),
    )


@pytest.mark.asyncio
async def test_empty_configuration_skips_all_external_checks() -> None:
    called = False

    async def handler(_context, _spec):
        nonlocal called
        called = True
        return ()

    result = await ExternalCheckRegistry({"qr": handler}).execute((), context())

    assert result == ()
    assert called is False


@pytest.mark.asyncio
async def test_registry_rejects_unknown_external_check() -> None:
    with pytest.raises(UnknownExternalCheck, match="missing"):
        await ExternalCheckRegistry({}).execute(
            (CapabilitySpec("missing", kind="EXTERNAL"),),
            context(),
        )
