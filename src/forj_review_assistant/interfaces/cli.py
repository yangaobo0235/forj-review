"""Command-line entrypoint for the review HTTP service."""

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="forj-review-assistant")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--reload", action="store_true")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--check-pages", type=Path, metavar="DIRECTORY", help="校验页面配置及运行时能力，不启动服务")
    commands.add_argument("--page-schema", action="store_true", help="输出页面配置 JSON Schema")
    commands.add_argument("--inspect-pages", type=Path, metavar="DIRECTORY", help="输出继承展开后的有效配置与摘要")
    commands.add_argument("--compare-pages", type=Path, nargs=2, metavar=("BEFORE", "AFTER"), help="比较两个目录的活动页面配置")
    commands.add_argument("--snapshot-pages", type=Path, metavar="DIRECTORY", help="保存本地配置快照并锁定已记录的版本内容")
    commands.add_argument("--replay-pages", type=Path, metavar="DIRECTORY", help="使用固定识别结果离线回放审核")
    parser.add_argument("--cases", type=Path, metavar="FILE", help="离线回放样例 JSON")
    parser.add_argument("--json", action="store_true", help="配置检查也输出结构化 JSON 诊断")
    args = parser.parse_args()
    if (args.replay_pages is not None) != (args.cases is not None):
        parser.error("--replay-pages 和 --cases 必须同时使用")
    if args.page_schema:
        from forj_review_assistant.businesses.configuration.schema import PageDefinition

        print(json.dumps(PageDefinition.model_json_schema(), ensure_ascii=False, indent=2))
        return
    if any((args.check_pages, args.inspect_pages, args.compare_pages,
            args.snapshot_pages, args.replay_pages)):
        from forj_review_assistant.businesses.configuration.history import record_snapshot
        from forj_review_assistant.businesses.configuration.inspection import compare_configurations
        from forj_review_assistant.interfaces.page_tools import (
            inspect_directory,
            replay_cases,
            validated_configuration,
        )

        result = None
        try:
            if args.check_pages:
                _, registry = validated_configuration(args.check_pages)
                result = {"ok": True, "active_pages": len(registry.active_profiles)}
            elif args.inspect_pages:
                result = inspect_directory(args.inspect_pages)
            elif args.compare_pages:
                result = compare_configurations(*(inspect_directory(p) for p in args.compare_pages))
            elif args.snapshot_pages:
                pages, _ = validated_configuration(args.snapshot_pages)
                result = {"ok": True, **record_snapshot(args.snapshot_pages, pages)}
            elif args.replay_pages:
                result = asyncio.run(replay_cases(args.replay_pages, args.cases))
        except (ValueError, LookupError, OSError) as exc:
            if args.json:
                print(json.dumps({"ok": False, "errors": [{
                    "code": "configuration_invalid", "message": str(exc),
                }]}, ensure_ascii=False))
                raise SystemExit(2) from exc
            parser.error(str(exc))
        if args.check_pages and not args.json:
            print(f"页面配置校验通过：{result['active_pages']} 个活动页面")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("ok") is False:
            raise SystemExit(1)
        return
    uvicorn.run(
        "forj_review_assistant.main:app", host=args.host, port=args.port, reload=args.reload,
        access_log=False,
    )
