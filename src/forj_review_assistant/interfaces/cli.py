"""Command-line entrypoint for the review HTTP service."""

import argparse
import json
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="forj-review-assistant")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--check-pages", type=Path, metavar="DIRECTORY", help="校验页面配置及运行时能力，不启动服务")
    parser.add_argument("--page-schema", action="store_true", help="输出页面配置 JSON Schema")
    args = parser.parse_args()
    if args.page_schema:
        from forj_review_assistant.businesses.configuration.schema import PageDefinition

        print(json.dumps(PageDefinition.model_json_schema(), ensure_ascii=False, indent=2))
        return
    if args.check_pages is not None:
        from forj_review_assistant.businesses.registry import build_business_registry
        from forj_review_assistant.runtime.review_service import ReviewService

        try:
            service = ReviewService(registry=build_business_registry(args.check_pages))
        except ValueError as exc:
            parser.error(str(exc))
        print(f"页面配置校验通过：{len(service.registry.active_profiles)} 个活动页面")
        return
    uvicorn.run(
        "forj_review_assistant.main:app", host=args.host, port=args.port, reload=args.reload
    )
