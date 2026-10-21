"""Structured configuration export, field maintenance, publication and rollback."""

import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path

from forj_review_service.businesses.page_config.state import ConfigurationState
from forj_review_service.config.database import load_connection_settings
from forj_review_service.integrations.database import Database
from forj_review_service.integrations.storage.configuration import (
    extract_receipt,
    historical_selection,
    publish_state,
    read_state,
    receipt_query,
)
from forj_review_service.integrations.storage.validation import save_manual_validation
from forj_review_service.runtime.configuration.offline import (
    prepare_maintenance,
    save_maintenance,
    verify_maintenance,
)
from forj_review_service.runtime.configuration.service import validated_snapshot


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_export(directory):
    data = read_json(directory / "configuration.json")
    if data.get("format") != "forj-relational-configuration" or data.get("version") != 1:
        raise ValueError("配置导出格式不受支持")
    return ConfigurationState(
        tuple(data["definitions"]), data["active"], data["revision"], data["release_id"]
    )


def export_state(directory, state):
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "configuration.json").write_text(
        json.dumps(
            {
                "format": "forj-relational-configuration",
                "version": 1,
                "definitions": state.definitions,
                "active": state.active,
                "revision": state.revision,
                "release_id": state.release_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("export", "prepare", "verify", "publish", "import", "restore")
    )
    parser.add_argument("--schema", default="local")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--page-id")
    parser.add_argument("--version")
    parser.add_argument("--rollback-version")
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--operations", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--trial-json", type=Path)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    if args.action in {"prepare", "verify"}:
        if not args.directory:
            parser.error("需要 --directory 指定结构化配置导出目录")
        state = read_export(args.directory)
        if args.action == "prepare":
            if not args.output_directory or not args.page_id:
                parser.error("prepare 需要 --output-directory 和 --page-id")
            candidate = read_json(args.candidate) if args.candidate else None
            plan = prepare_maintenance(
                state,
                args.page_id,
                args.version,
                read_json(args.operations) if args.operations else (),
                candidate=candidate,
                rollback=args.rollback_version,
            )
            export_state(args.output_directory, state)
            (args.output_directory / "plan.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result = plan
        else:
            if not args.trial_json or not args.cases:
                parser.error("verify 需要 --trial-json 和 --cases")
            evidence = read_json(args.trial_json)
            result = asyncio.run(
                verify_maintenance(
                    state,
                    read_json(args.directory / "plan.json"),
                    evidence.get("evidence", evidence),
                    read_json(args.cases),
                )
            )
    else:
        database = Database(load_connection_settings().conninfo(), args.schema)
        database.require_ready()
        with database.connect() as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            state = read_state(db)
        if args.action == "export":
            if not args.directory:
                parser.error("export 需要新的 --directory")
            export_state(args.directory, state)
            result = {
                "revision": state.revision,
                "release_id": state.release_id,
                "pages": len(state.active),
            }
        else:
            if not args.operation_id or args.expected_revision is None or not args.backup_dir:
                parser.error("写入需要 --operation-id、--expected-revision 和新的 --backup-dir")
            with database.connect() as db:
                prior = extract_receipt(db.orm.execute(receipt_query(args.operation_id)).first())
            if args.action == "publish":
                if not args.directory or not args.trial_json or not args.cases:
                    parser.error("publish 需要 --directory、--trial-json 和 --cases")
                plan = read_json(args.directory / "plan.json")
                if prior:
                    state = replace(state, receipts={args.operation_id: prior})
                evidence = read_json(args.trial_json)
                proposed, receipt = asyncio.run(
                    save_maintenance(
                        state,
                        plan,
                        evidence.get("evidence", evidence),
                        read_json(args.cases),
                        args.operation_id,
                    )
                )
            elif args.action == "restore":
                if not args.release_id or not args.trial_json or not args.cases:
                    parser.error("restore 需要 --release-id、--trial-json、--cases；每个变更页面须重新验证")
                with database.connect() as db:
                    proposed = historical_selection(db, state, args.release_id)
                evidence = read_json(args.trial_json)
                suite = read_json(args.cases)
                reports = []
                for page, version in proposed.active.items():
                    if state.active.get(page) == version:
                        continue
                    plan = prepare_maintenance(state, page, rollback=version)
                    reports.append(asyncio.run(verify_maintenance(
                        state, plan, evidence[page], suite)))
                if not reports:
                    raise ValueError("目标发布与当前配置相同，无需回滚")
                receipt = {"operation_id": args.operation_id, "rollback_target": args.release_id,
                           "verification": reports}
            else:
                if not args.directory:
                    parser.error("import 需要 --directory")
                incoming = read_export(args.directory)
                combined = {(d["page_id"], d["version"]): d for d in state.definitions}
                for d in incoming.definitions:
                    key = (d["page_id"], d["version"])
                    if key in combined and combined[key] != d:
                        raise ValueError("导入不能覆盖已有历史版本")
                    combined[key] = d
                proposed = replace(
                    state, definitions=tuple(combined.values()), active=incoming.active
                )
                receipt = {"operation_id": args.operation_id, "source": "developer_import"}
            validated_snapshot(proposed)
            if not prior and state.revision != args.expected_revision:
                raise ValueError("目标 revision 已改变，请重新导出验证")
            if prior:
                if args.action != "publish":
                    raise ValueError("操作 ID 已使用；请查询该发布并使用新的操作 ID")
                result = prior
            else:
                export_state(args.backup_dir, state)
                with database.connect() as db:
                    validation_id = None
                    if args.action in {"publish", "restore"}:
                        validation_id = save_manual_validation(
                            db, proposed, receipt, evidence,
                            purpose="ROLLBACK" if args.action == "restore" else "PUBLISH")
                    rid = publish_state(
                        db,
                        proposed,
                        operation_id=args.operation_id,
                        receipt=receipt,
                        source="MANUAL",
                        validation_id=validation_id,
                        confirmed_by="manual-cli",
                        operation_type={
                            "publish": "PUBLISH",
                            "import": "IMPORT",
                            "restore": "ROLLBACK",
                        }[args.action],
                        rollback_target=args.release_id,
                    )
                result = {**receipt, "release_id": rid, "state": "PUBLISHED"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
