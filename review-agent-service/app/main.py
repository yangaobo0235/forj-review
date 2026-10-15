"""审核 Agent HTTP API 入口。

主要职责：创建应用并暴露健康检查、同步审核和异步任务接口。
修改日期：2026-08-26
修改人：wuyi
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from fastapi import FastAPI, HTTPException, status

from app.businesses.context_validation import BusinessContextMismatch
from app.businesses.packs import build_collect_manifest, pack_for_business
from app.businesses.page_catalog import page_catalog
from app.businesses.registry import BusinessProfileNotFound
from app.contracts.review_schema import review_contract_schema
from app.models.review import (
    BusinessType,
    ReviewJobCreated,
    ReviewJobSnapshot,
    ReviewRequest,
    ReviewResponse,
)
from app.services.jobs import ReviewJobManager
from app.services.review import ReviewService
from app.workflow.config import load_dotenv


def configure_log_file(handler_logger: logging.Logger) -> None:
    """按 `REVIEW_LOG_DIR` 追加一个按天轮转的文件处理器；未设置时保持只输出控制台。

    这是**追加一条流，不是改一条流**：控制台输出必须保留，部署文档的排障流程
    整段建立在 `docker compose logs` 上，把日志搬走等于把那条路拆了。

    挂在 `uvicorn.error` 而不是 root：Uvicorn 给这个 logger 设了
    `propagate=False`，挂到 root 上一条都收不到。

    容器根文件系统是只读的，所以生产要落盘必须显式挂载可写卷；目录不可写时
    这里只降级成一条告警，不能让审核服务起不来。
    """
    # 先加载本地环境文件：`REVIEW_LOG_DIR` 通常写在 `.env` 里，而触发 dotenv 的
    # `ReviewService()` 要到本函数之后才构造。不先加载就读不到，配了也不生效。
    # `load_dotenv` 用 setdefault，进程环境变量仍然优先，重复调用幂等。
    load_dotenv()
    log_dir = os.getenv("REVIEW_LOG_DIR", "").strip()
    if not log_dir:
        return
    path = os.path.join(log_dir, "review-agent.log")
    target = os.path.abspath(path)
    if any(getattr(existing, "baseFilename", None) == target for existing in handler_logger.handlers):
        return
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = TimedRotatingFileHandler(
            path,
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
    except OSError as exc:
        handler_logger.warning(
            "Review log file disabled: dir=%s error_type=%s",
            log_dir,
            type(exc).__name__,
        )
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler_logger.addHandler(handler)
    handler_logger.info(
        "Review log file enabled: dir=%s rotation=daily backup_count=30",
        log_dir,
    )


app = FastAPI(title="车辆审核辅助 Agent")
# 使用 Uvicorn 已配置的 logger，确保 INFO 日志直接显示在启动终端。
logger = logging.getLogger("uvicorn.error")
# 先装配日志再创建服务：ReviewService 启动时会读配置、校验密钥，
# 那些提示只有进同一条流，排查时才不用在控制台和文件之间来回找。
configure_log_file(logger)
review_service = ReviewService()
review_jobs = ReviewJobManager(review_service)


def validate_business_profile(request: ReviewRequest) -> None:
    try:
        review_service.resolve_profile(request)
    except BusinessContextMismatch as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BusinessProfileNotFound as exc:
        raise HTTPException(
            status_code=422,
            detail="审核业务配置不存在或版本不兼容",
        ) from exc


@app.get("/health")
def health() -> dict[str, str]:
    """供扩展和部署探针检查 Agent 是否已启动。"""
    return {"status": "ok"}


@app.get("/api/review/schema")
def review_schema() -> dict[str, object]:
    """Expose the versioned request/response contract to integration clients."""
    return review_contract_schema()


@app.get("/api/review/collect-manifest")
def collect_manifest(business_type: BusinessType, region: str | None = None, profile_version: str | None = None, page_id: str | None = None) -> dict[str, object]:
    """下发页面采集清单：字段别名、图片分组标题和材料分组关键词。

    采集清单通常按业务包共用；传入地区、版本或 page_id 时会精确解析对应 Profile，
    让同业务多页面不会拿错清单。尚未声明清单的业务返回 404，前端退回内置表。
    """
    pack = pack_for_business(business_type)
    if region is not None or profile_version is not None or page_id is not None:
        from app.businesses.profiles import BUSINESS_PROFILES
        candidates = [p for p in BUSINESS_PROFILES if p.business_type == business_type and
                      (region is None or p.region.value == region) and
                      (profile_version is None or p.version == profile_version) and
                      (page_id is None or not page_id or p.page_id == page_id)]
        if not candidates:
            raise HTTPException(status_code=404, detail="该业务地区版本不存在")
        profile = candidates[-1]
        pack = profile.pack_declaration
    if pack is None:
        raise HTTPException(status_code=404, detail="该业务尚未声明采集清单")
    result = build_collect_manifest(pack)
    if region is not None or profile_version is not None or page_id is not None:
        result.update(region=profile.region.value, profile_version=profile.version)
    return result


@app.get("/api/review/page-catalog")
def review_page_catalog() -> dict[str, object]:
    """下发页面识别清单：管理端审核页地址 → 业务、地区、页面特征文案。

    必须独立于采集清单：识别发生在「按业务类型取采集清单」之前，识别不出来
    就不知道该取哪份清单。因此这里一次下发全部业务，而不是按业务类型查。
    """
    return page_catalog()


@app.post("/api/review/assist", response_model=ReviewResponse)
def assist(request: ReviewRequest) -> ReviewResponse:
    """执行一次只读审核辅助，不会修改原审核系统。"""
    validate_business_profile(request)
    logger.info(
        "Review request received: trace_id=%s application_id=%s field_count=%d image_count=%d",
        request.trace_id or "generated",
        request.application_id or "unknown",
        len(request.page_fields),
        len(request.images),
    )
    response = review_service.assist(request)
    logger.info(
        "Review completed: trace_id=%s recommendation=%s comparison_count=%d issue_count=%d",
        response.trace_id,
        response.recommendation,
        len(response.comparisons),
        len(response.issues),
    )
    return response


@app.post(
    "/api/review/jobs",
    response_model=ReviewJobCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_review_job(request: ReviewRequest) -> ReviewJobCreated:
    validate_business_profile(request)
    logger.info(
        "Review job requested: trace_id=%s application_id=%s image_count=%d",
        request.trace_id or "generated",
        request.application_id or "unknown",
        len(request.images),
    )
    try:
        created = review_jobs.create(request)
    except ValueError as exc:
        # 请求本身不合法返回 422。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("Review job created: job_id=%s", created.job_id)
    return created


@app.post(
    "/api/review/jobs/urls",
    response_model=ReviewJobCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_url_review_job(request: ReviewRequest) -> ReviewJobCreated:
    """直接提交 DOM 采集的图片地址，不下载、压缩或接收图片正文。"""
    validate_business_profile(request)
    try:
        created = review_jobs.create_urls(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("URL review job created: job_id=%s image_count=%d", created.job_id, len(request.images))
    return created


@app.delete(
    "/api/review/jobs/{job_id}",
    response_model=ReviewJobSnapshot,
)
def cancel_review_job(job_id: str) -> ReviewJobSnapshot:
    try:
        return review_jobs.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="审核任务不存在或已过期") from exc


@app.get("/api/review/jobs/{job_id}", response_model=ReviewJobSnapshot)
def get_review_job(job_id: str) -> ReviewJobSnapshot:
    try:
        return review_jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="审核任务不存在或已过期") from exc
