"""Persist completed manual evidence without copying snapshots or candidates."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from forj_review_database.models import Page, ValidationRun
from forj_review_database.validation import ValidationReport
from forj_review_service.businesses.page_config.inspection import digest
from forj_review_service.businesses.page_config.kernel import KERNEL_VERSION


def save_manual_validation(connection, state, receipt, evidence, *, purpose):
    verification = receipt["verification"]
    reports = verification if isinstance(verification, list) else [verification]
    if not reports or any(not r["trial"]["ok"] or not r["replay"]["ok"] for r in reports):
        raise ValueError("人工发布必须完成试采集和独立回放")
    session = getattr(connection, "orm", connection)
    page_id = session.scalar(select(Page.id).where(Page.page_code == receipt.get("page_id")))
    report = ValidationReport(
        checks=[dict(page_code=r["page_id"], target_kind="PAGE", target_key=r["page_id"],
                     check_code="MANUAL_VALIDATION", status="PASSED", message="试采集与回放通过")
                for r in reports],
        collection={"evidence_digest": digest(evidence),
                    "results": [r["trial"] for r in reports]},
        replays=[r["replay"] for r in reports],
        summary={k: receipt[k] for k in ("page_id", "version", "candidate_digest", "effective_digest")
                 if k in receipt},
    )
    now = datetime.now(UTC)
    identifier = uuid4().hex
    session.add(ValidationRun(
        id=identifier, purpose=purpose, base_release_id=state.release_id,
        candidate_page_id=page_id, candidate_digest=receipt.get("candidate_digest"),
        manifest_digest=digest({page: digest(state.source(page, version))
                                for page, version in sorted(state.active.items())}),
        digest_version="manifest-v1", scene_digest=digest(evidence),
        kernel_version=KERNEL_VERSION, kernel_fingerprint=KERNEL_VERSION,
        report_schema_version="1.0", status="PASSED", report=report.model_dump(mode="json"),
        performed_by="manual-cli", started_at=now, completed_at=now,
    ))
    session.flush()
    return identifier
