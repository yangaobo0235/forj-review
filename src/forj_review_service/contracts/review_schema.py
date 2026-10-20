"""Generate the versioned review request/response JSON schema.

Keeping schema generation next to the Pydantic models makes the API contract
discoverable to frontend and integration tooling without duplicating model
definitions in a hand-maintained JSON file.
"""

from typing import Any

from forj_review_service.contracts.review import (
    CapabilityPlanEntry,
    CapabilityResult,
    EvidenceFact,
    FieldComparison,
    PageActionIntent,
    ReviewRequest,
    ReviewResponse,
    ReviewTask,
)


def review_contract_schema() -> dict[str, Any]:
    """Return a self-contained schema bundle for the v2 review protocol."""

    return {
        "protocol_version": "2.0",
        "request": ReviewRequest.model_json_schema(),
        "response": ReviewResponse.model_json_schema(),
        "components": {
            "ReviewTask": ReviewTask.model_json_schema(),
            "CapabilityPlanEntry": CapabilityPlanEntry.model_json_schema(),
            "CapabilityResult": CapabilityResult.model_json_schema(),
            "EvidenceFact": EvidenceFact.model_json_schema(),
            "FieldComparison": FieldComparison.model_json_schema(),
            "PageActionIntent": PageActionIntent.model_json_schema(),
        },
    }
