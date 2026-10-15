"""Command-line entrypoint for the review HTTP service."""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="forj-review-assistant")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "forj_review_assistant.main:app", host=args.host, port=args.port, reload=args.reload
    )
