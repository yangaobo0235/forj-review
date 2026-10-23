"""Explicit developer entry point. This process is separate from the online service."""

import argparse
import asyncio
import json
from pathlib import Path

from forj_review_config_service.contracts.handoff import HandoffPackage
from forj_review_config_service.local_development.checks import (
    remove_owned_container,
    validate_checks,
)
from forj_review_config_service.local_development.workspace import prepare, save_state


async def execute(args):
    from forj_review_config_service.config.settings import DEFAULT_ENV_FILE, Settings
    from forj_review_config_service.integrations.conversation.client import ConversationClient
    from forj_review_config_service.local_development.runner import run

    root = args.output.resolve()
    if args.resume:
        state = json.loads((root / "state.json").read_text(encoding="utf8"))
        HandoffPackage.model_validate(state["package"]).verified()
        state["checks"] = validate_checks(state["checks"])
        if args.reconcile:
            if not await remove_owned_container(root, state):
                raise ValueError("任务容器尚未清理，已保留所有权和未知结果，请恢复 Docker 后重试")
            completed = {m.get("tool_call_id") for m in state["messages"] if m["role"] == "tool"}
            for message in list(state["messages"]):
                for call in message.get("tool_calls", []):
                    if call["id"] not in completed:
                        state["messages"].append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": json.dumps(
                                    {
                                        "status": "INTERRUPTED_UNKNOWN_RESULT",
                                        "next_step": "重新读取文件和证据后决定；不盲目重放",
                                    }
                                ),
                            }
                        )
            state.pop("inflight", None)
            state.pop("delivery", None)
            save_state(root, state)
    else:
        if not args.package or not args.repo or not args.checks or not args.image:
            raise ValueError("新任务须明确 package、repo、checks 和 image")
        package = HandoffPackage.model_validate_json(
            args.package.read_text(encoding="utf8")
        ).verified()
        checks = validate_checks(json.loads(args.checks.read_text(encoding="utf8")))
        state = prepare(args.repo, root, package, checks, args.image)
    # Load only model settings; do not resolve or pass database credentials to the runner.
    model = ConversationClient(Settings(_env_file=DEFAULT_ENV_FILE))
    try:
        result = await run(root, state, model, max_calls=args.max_calls)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "report": str(root / "report.json"),
                    "patch": str(root / "changes.patch"),
                },
                ensure_ascii=False,
            )
        )
    finally:
        await model.close()


def main():
    parser = argparse.ArgumentParser(description="开发本地接续：独立副本、受控检查、补丁与报告")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checks", type=Path, help="开发者确定的独立检查 JSON 数组")
    parser.add_argument("--image", help="已准备依赖的本地 Docker 镜像，不自动拉取")
    parser.add_argument("--max-calls", type=int, default=30, choices=range(1, 101))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--reconcile", action="store_true", help="核对副本、终止遗留容器并将未知工具回执标为中断"
    )
    args = parser.parse_args()
    if args.reconcile and not args.resume:
        parser.error("--reconcile 必须与 --resume 一起使用")
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
