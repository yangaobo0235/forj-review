"""Independent local configuration service entry point."""

import argparse
import json

import uvicorn


def main():
    parser = argparse.ArgumentParser(prog="forj-configuration-agent")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--recover", action="store_true", help="恢复未完成文件事务，默认回退未提交内容")
    args = parser.parse_args()
    from forj_configuration_agent.bootstrap.application import create_app
    from forj_configuration_agent.config.settings import load_settings
    settings = load_settings()
    if args.recover:
        from forj_configuration_agent.integrations.review_kernel.library import ReviewKernel
        print(json.dumps(ReviewKernel(settings.pages_dir, settings.cases_dir, settings.review_url).recover()))
        return
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
