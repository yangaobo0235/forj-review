"""Page aggregates and atomic releases; JSON files are never database entities."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from forj_review_database.models import (
    Head,
    Page,
    PageField,
    PageMaterial,
    PageVersion,
    Release,
    ReleaseItem,
    ValidationRun,
)
from forj_review_service.businesses.page_config.errors import PublicationConflict
from forj_review_service.businesses.page_config.inspection import digest
from forj_review_service.businesses.page_config.kernel import (
    KERNEL_CONTRACT_VERSION,
    KERNEL_VERSION,
)
from forj_review_service.businesses.page_config.row_contract import expand_options, split_definition
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.page_config.state import ConfigurationState

READ_QUERIES = (
    select(Head.revision, Head.release_id, Release.manifest_digest, Release.minimum_kernel_version)
    .outerjoin(Release, Release.id == Head.release_id)
    .where(Head.id == 1),
    select(
        Page.page_code,
        Page.business_type,
        PageVersion.region_code,
        PageVersion.id,
        PageVersion.version_no,
        PageVersion.schema_version,
        PageVersion.title,
        PageVersion.page_options,
        PageVersion.content_digest,
        PageVersion.enabled,
    )
    .join(Page, Page.id == PageVersion.page_id)
    .order_by(Page.page_code, PageVersion.version_no),
    select(
        PageField.page_version_id,
        PageField.field_key,
        PageField.label,
        PageField.reviewable,
        PageField.required,
        PageField.writable,
        PageField.normalizer,
        PageField.options,
    ).order_by(PageField.page_version_id, PageField.sort_order),
    select(
        PageMaterial.page_version_id,
        PageMaterial.material_code,
        PageMaterial.label,
        PageMaterial.guidance,
        PageMaterial.options,
    ).order_by(PageMaterial.page_version_id, PageMaterial.sort_order),
    select(Page.page_code, PageVersion.version_no)
    .select_from(Head)
    .join(ReleaseItem, ReleaseItem.release_id == Head.release_id)
    .join(Page, Page.id == ReleaseItem.page_id)
    .join(PageVersion, PageVersion.id == ReleaseItem.page_version_id)
    .where(Head.id == 1),
)


def assemble_state(rows):
    heads, versions, fields, materials, selections = rows
    if not heads:
        raise ValueError("当前 Schema 未初始化配置发布指针")
    revision, release_id, manifest_digest, minimum = heads[0]
    if minimum:
        import re

        if not re.fullmatch(r"\d+\.\d+\.\d+", minimum) or tuple(
            map(int, minimum.split("."))
        ) > tuple(map(int, KERNEL_CONTRACT_VERSION.split("."))):
            raise ValueError("发布要求更高的框架契约版本，请先更新服务代码")
    documents = {}
    expected = {}
    for code, business, region, vid, version, schema, title, options, checksum, enabled in versions:
        documents[vid] = {
            **expand_options(options, region=region, version=version),
            "schema_version": schema,
            "page_id": code,
            "business_type": business,
            "region": region,
            "version": version,
            "title": title,
            "enabled": enabled,
            "fields": [],
            "materials": [],
        }
        expected[vid] = checksum
    for vid, key, label, reviewable, required, writable, normalizer, options in fields:
        documents[vid]["fields"].append(
            {
                **options,
                "key": key,
                "label": label,
                "reviewable": reviewable,
                "required": required,
                "writable": writable,
                "normalizer": normalizer,
            }
        )
    for vid, code, label, guidance, options in materials:
        documents[vid]["materials"].append(
            {
                **options,
                "document_type": code,
                "label": label,
                "guidance": guidance,
            }
        )
    for vid, definition in documents.items():
        normalized = PageDefinition.model_validate(definition).model_dump(
            mode="json", exclude={"active"}
        )
        if digest(normalized) != expected[vid]:
            raise ValueError("数据库页面版本内容摘要不一致")
        documents[vid] = normalized
    state = ConfigurationState(tuple(documents.values()), dict(selections), revision, release_id)
    if (
        release_id is not None
        and digest(
            {
                page: digest(state.source(page, version))
                for page, version in sorted(state.active.items())
            }
        )
        != manifest_digest
    ):
        raise ValueError("当前发布的页面选择摘要不一致")
    return state


def state_queries(active_only=False):
    if not active_only:
        return READ_QUERIES
    selected = (
        select(ReleaseItem.page_version_id)
        .join(Head, Head.release_id == ReleaseItem.release_id)
        .where(Head.id == 1)
    )
    return (
        READ_QUERIES[0],
        READ_QUERIES[1].where(PageVersion.id.in_(selected)),
        READ_QUERIES[2].where(PageField.page_version_id.in_(selected)),
        READ_QUERIES[3].where(PageMaterial.page_version_id.in_(selected)),
        READ_QUERIES[4],
    )


def read_state(connection, *, active_only=False):
    """Synchronous maintenance operations use the same ORM mappings and transaction."""
    session = getattr(connection, "orm", connection)
    return assemble_state([session.execute(query).all() for query in state_queries(active_only)])


async def read_state_async(session, *, active_only=False):
    rows = []
    for query in state_queries(active_only):
        rows.append((await session.execute(query)).all())
    return assemble_state(rows)


def receipt_query(operation_id):
    return (
        select(
            Release.id,
            Release.operation_id,
            Page.page_code,
            PageVersion.version_no,
            ValidationRun.candidate_digest,
            ValidationRun.kernel_version,
            ValidationRun.report,
        )
        .select_from(Release)
        .join(ValidationRun, ValidationRun.id == Release.validation_run_id)
        .join(ReleaseItem, ReleaseItem.release_id == Release.id)
        .join(Page, Page.id == ReleaseItem.page_id)
        .join(PageVersion, PageVersion.id == ReleaseItem.page_version_id)
        .where(Release.operation_id == operation_id, Page.id == ValidationRun.candidate_page_id)
    )


def extract_receipt(row):
    if not row:
        return None
    rid, op, page, version, candidate, kernel, report = row
    return dict(
        release_id=rid,
        operation_id=op,
        page_id=page,
        version=version,
        candidate_digest=candidate,
        kernel_version=kernel,
        effective_digest=report["summary"]["effective_digest"],
        state="PUBLISHED",
    )


def _publish_state(
    session: Session,
    state: ConfigurationState,
    *,
    operation_id,
    source="developer_import",
    operator="local",
    receipt=None,
    operation_type="IMPORT",
    rollback_target=None,
    source_session=None,
    validation_id=None,
    confirmed_by=None,
    change_reason="配置维护",
) -> int:
    """ORM transaction shared by asynchronous services and explicit maintenance.

    The head lock serializes publication. Recheck operation identity AFTER taking
    it so concurrent retries of the same operation receive the same release.
    """
    request_digest = digest(
        {"definitions": state.definitions, "active": state.active, "receipt": receipt or {}}
    )
    head = session.execute(
        select(Head.revision, Head.release_id).where(Head.id == 1).with_for_update()
    ).all()
    prior = session.execute(
        select(Release.id, Release.request_digest).where(Release.operation_id == operation_id)
    ).all()
    if prior:
        if prior[0][1] != request_digest:
            raise PublicationConflict("IDEMPOTENCY_CONFLICT", "发布操作 ID 已用于其他内容")
        return prior[0][0]
    if not head or head[0][0] != state.revision:
        raise PublicationConflict("BASELINE_CHANGED", "配置基线发生变化，请重新验证候选")
    page_ids, version_ids = ({}, {})
    for definition in state.definitions:
        d = PageDefinition.model_validate(definition).model_dump(mode="json", exclude={"active"})
        code, version = (d["page_id"], d["version"])
        found = session.execute(
            select(Page.id, Page.business_type).where(Page.page_code == code)
        ).all()
        if found:
            pid, business = found[0]
            if business != d["business_type"]:
                raise PublicationConflict("PAGE_IDENTITY_IMMUTABLE", "已有页面的业务身份不能改变")
        else:
            inserted = session.execute(
                insert(Page)
                .values(page_code=code, business_type=d["business_type"], created_by=operator)
                .returning(Page.id)
            ).all()
            pid = inserted[0][0]
        page_ids[code] = pid
        found = session.execute(
            select(PageVersion.id, PageVersion.content_digest).where(
                PageVersion.page_id == pid, PageVersion.version_no == version
            )
        ).all()
        checksum = digest(d)
        if found:
            vid, existing = found[0]
            if existing != checksum:
                raise PublicationConflict("VERSION_IMMUTABLE", "历史版本不可覆盖，请创建新版本")
        else:
            options = split_definition(d)
            base = session.execute(
                select(ReleaseItem.page_version_id).where(
                    ReleaseItem.release_id == head[0][1], ReleaseItem.page_id == pid
                )
            ).all()
            inserted = session.execute(
                insert(PageVersion)
                .values(
                    page_id=pid,
                    version_no=version,
                    schema_version=d["schema_version"],
                    digest_version="page-v2",
                    base_page_version_id=base[0][0] if base else None,
                    title=d["title"],
                    region_code=d["region"],
                    enabled=d.get("enabled", True),
                    change_reason=change_reason,
                    page_options=options,
                    content_digest=checksum,
                    created_by=operator,
                )
                .returning(PageVersion.id)
            ).all()
            vid = inserted[0][0]
            for order, f in enumerate(d["fields"]):
                extras = {
                    k: v
                    for k, v in f.items()
                    if k not in {"key", "label", "reviewable", "required", "writable", "normalizer"}
                }
                session.execute(
                    insert(PageField).values(
                        page_version_id=vid,
                        field_key=f["key"],
                        label=f["label"],
                        sort_order=order,
                        reviewable=f["reviewable"],
                        required=f["required"],
                        writable=f["writable"],
                        normalizer=f["normalizer"],
                        options=extras,
                    )
                )
            for order, m in enumerate(d["materials"]):
                extras = {
                    k: v
                    for k, v in m.items()
                    if k
                    not in {
                        "document_type",
                        "label",
                        "required",
                        "minimum_count",
                        "guidance",
                        "required_pages",
                        "alternative_group",
                        "remove_extra_fields",
                    }
                }
                session.execute(
                    insert(PageMaterial).values(
                        page_version_id=vid,
                        material_code=m["document_type"],
                        label=m["label"],
                        sort_order=order,
                        guidance=m["guidance"],
                        options=extras,
                    )
                )
        version_ids[code, version] = vid
    if not state.active or any(
        ((page, version) not in version_ids for page, version in state.active.items())
    ):
        raise PublicationConflict("INVALID_SELECTION", "发布必须明确选择有效的页面版本")
    manifest_digest = digest(
        {
            page: digest(
                next(
                    (
                        d
                        for d in state.definitions
                        if (d["page_id"], d["version"]) == (page, version)
                    )
                )
            )
            for page, version in sorted(state.active.items())
        }
    )
    if validation_id is None:
        if operation_type != "IMPORT":
            raise PublicationConflict("VALIDATION_REQUIRED", "发布必须关联独立完成验证")
        from forj_review_service.businesses.page_config.loader import load_definitions

        load_definitions(state.definitions, state.active)
        validation_id = uuid4().hex
        session.execute(
            insert(ValidationRun).values(
                id=validation_id,
                purpose="IMPORT",
                base_release_id=head[0][1],
                manifest_digest=manifest_digest,
                digest_version="manifest-v1",
                kernel_version=KERNEL_VERSION,
                kernel_fingerprint=KERNEL_VERSION,
                report_schema_version="1.0",
                status="PASSED",
                report={
                    "checks": [],
                    "collection": None,
                    "replays": [],
                    "summary": {
                        "message": "维护导入通过完整内核编译；不声称浏览器试采集或业务回放"
                    },
                },
                performed_by=operator,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
    else:
        proof = session.execute(
            select(
                ValidationRun.status,
                ValidationRun.manifest_digest,
                ValidationRun.base_release_id,
                ValidationRun.kernel_version,
            ).where(ValidationRun.id == validation_id)
        ).all()
        if not proof or tuple(proof[0]) != ("PASSED", manifest_digest, head[0][1], KERNEL_VERSION):
            raise PublicationConflict("STALE_VALIDATION", "验证报告与发布候选、基线或内核不一致")
    inserted = session.execute(
        insert(Release)
        .values(
            release_no=uuid4().hex,
            base_release_id=head[0][1],
            rollback_target_release_id=rollback_target,
            operation_type=operation_type,
            source={"developer_import": "MANUAL", "configuration_workbench": "ASSISTANT"}.get(
                source, source
            ),
            source_session_id=source_session,
            operation_id=operation_id,
            request_digest=request_digest,
            manifest_digest=manifest_digest,
            validation_run_id=validation_id,
            digest_version="manifest-v1",
            minimum_kernel_version=KERNEL_CONTRACT_VERSION,
            created_by=operator,
            confirmed_by=confirmed_by or operator,
            confirmed_at=datetime.now(UTC),
            change_reason=change_reason,
        )
        .returning(Release.id)
    ).all()
    rid = inserted[0][0]
    for code, version in sorted(state.active.items()):
        session.execute(
            insert(ReleaseItem).values(
                release_id=rid, page_id=page_ids[code], page_version_id=version_ids[code, version]
            )
        )
    session.execute(
        update(Head)
        .where(Head.id == 1)
        .values(release_id=rid, revision=Head.revision + 1, updated_at=func.now())
    )
    return rid


def publish_state(connection, state, **kwargs):
    return _publish_state(getattr(connection, "orm", connection), state, **kwargs)


async def publish_state_async(session: AsyncSession, state: ConfigurationState, **kwargs) -> int:
    # SQLAlchemy adapts ORM I/O to this AsyncSession; no worker or second transaction is opened.
    return await session.run_sync(_publish_state, state, **kwargs)


def historical_query(release_id):
    return (
        select(Page.page_code, PageVersion.version_no)
        .select_from(ReleaseItem)
        .join(Page, Page.id == ReleaseItem.page_id)
        .join(PageVersion, PageVersion.id == ReleaseItem.page_version_id)
        .where(ReleaseItem.release_id == release_id)
    )


def historical_selection(connection, state, release_id):
    rows = getattr(connection, "orm", connection).execute(historical_query(release_id)).all()
    if not rows:
        raise ValueError("历史发布不存在")
    return replace(state, active=dict(rows))
