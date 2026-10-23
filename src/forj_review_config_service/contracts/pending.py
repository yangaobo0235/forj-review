"""Discriminated durable interactions; all variants remain JSON objects on the wire."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue
from typing_extensions import NotRequired, TypedDict


class ToolInteraction(TypedDict):
    id: str
    tool_call_id: str
    revision: int
    intent_revision: NotRequired[int]


class QuestionPending(ToolInteraction):
    kind: Literal["question"]
    question: str
    options: list[str]
    answer: NotRequired[str]


class BrowserPending(ToolInteraction):
    kind: Literal["browser"]
    manifest: dict[str, JsonValue]
    candidate_digest: str
    page_catalog: dict[str, JsonValue]
    page_id: str
    snapshot_id: str
    structure_digest: str
    page_instance_id: str
    page_url: str
    expires_at: str
    binding: NotRequired[dict[str, JsonValue]]  # Older tasks must be recollected.
    result: NotRequired[dict[str, JsonValue]]
    manual: NotRequired[bool]


class ConfirmationPending(TypedDict):
    kind: Literal["confirmation"]
    id: str
    intent_digest: NotRequired[str]
    intent_revision: NotRequired[int]
    confirmed: NotRequired[bool]
    candidate_digest: NotRequired[str]
    operation_id: NotRequired[str]


Pending = Annotated[
    QuestionPending | BrowserPending | ConfirmationPending, Field(discriminator="kind")
]
