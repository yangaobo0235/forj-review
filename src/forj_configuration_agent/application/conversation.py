"""Durable tool identities and a business-only projection of the conversation."""

import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from forj_configuration_agent.contracts.models import AgentError


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    arguments: str


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: str = Field(min_length=1)
    function: FunctionCall


def normalize_reply(message: dict) -> dict:
    """Allocate identities once, before persistence; resumption reuses these identities."""
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise AgentError("MODEL_FORMAT", "模型返回的消息格式无效", 422)
    content, calls = message.get("content") or "", message.get("tool_calls", [])
    if not isinstance(content, str) or not isinstance(calls, list) or len(calls) > 120:
        raise AgentError("MODEL_FORMAT", "模型返回的消息或工具调用格式无效", 422)
    result = {"role": "assistant", "content": content, "_response_id": uuid4().hex}
    normalized, metadata = [], {}
    try:
        for raw in calls:
            call = ToolCall.model_validate(raw)
            internal_id = "call_" + uuid4().hex
            normalized.append({"id": internal_id, "type": "function", "function": call.function.model_dump()})
            metadata[internal_id] = {"provider_call_id": call.id, "name": call.function.name,
                                     "arguments_digest": fingerprint(call.function.arguments)}
    except ValidationError as exc:
        raise AgentError("MODEL_FORMAT", "模型返回的工具调用格式无效", 422) from exc
    if normalized:
        result.update(tool_calls=normalized, _tool_metadata=metadata)
    if not content and not normalized:
        raise AgentError("MODEL_FORMAT", "模型返回空内容，可以重试", 422)
    return result


def tool_message(record, call_id, result):
    if not any(m.get("role") == "tool" and m.get("tool_call_id") == call_id for m in record.messages):
        record.messages.append({"role": "tool", "tool_call_id": call_id,
                                "content": json.dumps(result, ensure_ascii=False)})


def chat_messages(record) -> list[dict]:
    """Project answered questions from durable protocol, including older sessions."""
    answers = {m.get("tool_call_id"): m for m in record.messages if m.get("role") == "tool"}
    result = []
    for message in record.messages:
        content = message.get("content")
        if message.get("role") in {"assistant", "user"} and isinstance(content, str) and content.strip():
            if not content.startswith("系统强制验证结果："):
                result.append({"role": message["role"], "content": content.split("\nrequirement_id=", 1)[0]})
        for call in message.get("tool_calls", []):
            try:
                if call["function"]["name"] != "ask_user" or call["id"] not in answers:
                    continue
                question = json.loads(call["function"]["arguments"])["question"]
                answer = json.loads(answers[call["id"]]["content"]).get("answer")
                if isinstance(question, str) and isinstance(answer, str) and answer.strip():
                    if not (result and result[-1] == {"role": "assistant", "content": question}):
                        result.append({"role": "assistant", "content": question})
                    result.append({"role": "user", "content": answer})
            except (ValueError, KeyError, TypeError):
                continue
    return result
