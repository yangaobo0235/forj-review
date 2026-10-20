"""Independent local configuration service entry point."""

import argparse
import json
import sys

import uvicorn


def main():
    parser = argparse.ArgumentParser(prog="forj-review-config-service")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26102)
    parser.add_argument("--check-storage", action="store_true", help="只读检查 PostgreSQL 存储结构")
    args = parser.parse_args()
    from forj_review_config_service.bootstrap.application import create_app
    from forj_review_config_service.config.settings import load_settings

    settings = load_settings()
    if args.check_storage:
        from forj_review_config_service.integrations.database import Database

        database = Database(settings.database_url, settings.database_schema)
        database.require_ready()
        print(json.dumps({"status": "READY"}))
        return
    # Uvicorn creates its own loop; a process-level policy does not override it.
    # psycopg async connections require a selector loop on Windows.
    loop = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False, loop=loop)


if __name__ == "__main__":
    main()
