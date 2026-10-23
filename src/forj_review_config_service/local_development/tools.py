"""Local investigation and edits inside the task copy, with frozen check commands."""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from forj_review_config_service.contracts.handoff import package_digest
from forj_review_config_service.local_development.checks import run_check
from forj_review_config_service.local_development.workspace import (
    EDITABLE,
    allowed,
    editable,
    iter_files,
    reconcile,
    safe_path,
)


class LocalToolError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_EDIT"):
        self.message, self.code = message, code
        super().__init__(message)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchArguments(Arguments):
    text: str = Field(min_length=1, max_length=200)


class FileArguments(Arguments):
    path: str = Field(min_length=1, max_length=1000)
    offset: int = Field(default=0, ge=0)


class WriteArguments(Arguments):
    path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(max_length=64)
    content: str = Field(max_length=100000)


class ReplaceArguments(Arguments):
    path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(max_length=64)
    old: str = Field(min_length=1, max_length=100000)
    new: str = Field(max_length=100000)


class CheckArguments(Arguments):
    check_id: str = Field(min_length=1, max_length=200)


class ResultArguments(Arguments):
    tool_result_id: str = Field(min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0)


class FinishArguments(Arguments):
    status: Literal["PATCH_READY", "NEEDS_INPUT", "HUMAN_HANDOFF"]
    summary: str = Field(min_length=1, max_length=16000)
    obstacles: list[str] = Field(max_length=100)
    evidence_refs: list[str] = Field(max_length=100)


TOOL_ARGUMENTS = {
    "search": SearchArguments,
    "read_file": FileArguments,
    "write_file": WriteArguments,
    "replace_text": ReplaceArguments,
    "run_check": CheckArguments,
    "read_tool_result": ResultArguments,
    "finish": FinishArguments,
}


def tool(name, description):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": TOOL_ARGUMENTS[name].model_json_schema(),
        },
    }


def local_tools():
    return [
        tool("search", "搜索任务副本的文本，结果最多 60 项"),
        tool("read_file", "分页读文件，返回当前完整文件 sha256"),
        tool("write_file", "精确写文件；新文件 sha256 为空；不能修改冻结测试"),
        tool("replace_text", "精确替换唯一一处原文；先读文件取得摘要，避免重写整个文件"),
        tool("run_check", "运行开发者选定的独立检查"),
        tool("read_tool_result", "分页读取已保存工具证据"),
        tool("finish", "结束本地调查；补丁状态仍由独立检查确定"),
    ]


async def execute_tool(root, state, name: str, args: object) -> dict:
    if name not in TOOL_ARGUMENTS:
        raise LocalToolError("未注册的本地工具", "UNKNOWN_TOOL")
    try:
        args = TOOL_ARGUMENTS[name].model_validate(args).model_dump()
    except ValidationError as exc:
        raise LocalToolError("工具参数不符合登记契约", "INVALID_ARGUMENTS") from exc
    work = root / "work"
    if name == "read_tool_result":
        evidence = next(
            (e for e in state["evidence"] if e.get("id") == args["tool_result_id"]), None
        )
        if evidence is None:
            raise LocalToolError("工具证据不存在", "EVIDENCE_NOT_FOUND")
        value = json.dumps(evidence["result"], ensure_ascii=False)
        offset = args.get("offset", 0)
        return {
            "content": value[offset : offset + 4000],
            "next_offset": offset + 4000 if len(value) > offset + 4000 else None,
        }
    if name == "search":
        needle = args["text"]
        matches = []
        for path in iter_files(work):
            relative = path.relative_to(work).as_posix()
            if not allowed(relative):
                continue
            try:
                lines = path.read_text(encoding="utf8").splitlines()
            except UnicodeError:
                continue
            for i, line in enumerate(lines, 1):
                if needle.casefold() in line.casefold():
                    matches.append({"path": relative, "line": i, "text": line[:400]})
                    if len(matches) >= 60:
                        return {"query": needle, "matches": matches, "truncated": True}
        return {"query": needle, "matches": matches, "truncated": False}
    if name in {"read_file", "write_file", "replace_text"}:
        relative = args["path"]
        if not allowed(relative):
            raise LocalToolError("该文件不属于可读取任务资料", "FILE_NOT_ALLOWED")
        path = safe_path(work, relative)
        if name == "read_file" and not path.is_file():
            raise LocalToolError("文件不存在，请先搜索实际路径", "FILE_NOT_FOUND")
        prior = path.read_bytes() if path.is_file() else b""
        sha = hashlib.sha256(prior).hexdigest() if path.exists() else ""
        if name == "read_file":
            offset = args.get("offset", 0)
            content = prior.decode("utf8").replace("\r\n", "\n")
            return {
                "path": relative,
                "sha256": sha,
                "content": content[offset : offset + 3000],
                "next_offset": offset + 3000 if len(content) > offset + 3000 else None,
            }
        if PurePosixPath(relative).parts[0] not in EDITABLE:
            raise LocalToolError("仅允许修改源码、前端、契约和业务说明", "FILE_NOT_ALLOWED")
        if not editable(relative):
            raise LocalToolError(
                "已有验收与验证器冻结；新增回归请通过独立开发评审补充", "FROZEN_ACCEPTANCE"
            )
        if name == "replace_text":
            old = args["old"]
            content = prior.decode("utf8").replace("\r\n", "\n")
            if not old or content.count(old) != 1:
                raise LocalToolError(
                    "原文必须精确匹配唯一位置；请使用读取结果中的原始文本，不要保留 JSON 转义符"
                )
            args["content"] = content.replace(old, args["new"], 1)
        if args["sha256"] != sha or len(args["content"]) > 100000:
            raise LocalToolError(
                "文件摘要变化或内容过大，请重新读取文件取得实际 sha256，不能编造摘要"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        content = args["content"].replace("\r\n", "\n")
        newline = "\r\n" if b"\r\n" in prior else "\n"
        path.write_bytes(content.replace("\n", newline).encode("utf8"))
        reconcile(root, state)
        return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if name == "run_check":
        check = next((c for c in state["checks"] if c["id"] == args["check_id"]), None)
        if not check:
            raise LocalToolError("未登记的检查", "UNKNOWN_CHECK")
        reconcile(root, state)
        return await run_check(root, state, check)
    if name == "finish":
        refs = {e["id"] for e in state["evidence"] if "id" in e}
        if set(args["evidence_refs"]) - refs:
            raise LocalToolError("交付必须引用实际调查证据", "INVALID_EVIDENCE")
        if args["status"] == "HUMAN_HANDOFF":
            investigated = {
                package_digest(e["result"])
                for e in state["evidence"]
                if e.get("tool") in {"read_file", "search"}
                and e.get("id") in args["evidence_refs"]
                and e.get("result", {}).get("status") != "FAILED"
            }
            if not args["obstacles"] or len(investigated) < 2:
                raise LocalToolError(
                    "人工交接需具体障碍及至少两项不同的实际读取/搜索依据",
                    "HANDOFF_EVIDENCE_REQUIRED",
                )
        return {"finish": args}
    raise LocalToolError("未注册的本地工具", "UNKNOWN_TOOL")
