import asyncio
import logging
import os
import threading

import pytest

from forj_review_assistant.contracts.review import ReviewRequest
from forj_review_assistant.observability.logging import configure_log_file
from forj_review_assistant.runtime.review_service import ReviewService


@pytest.mark.parametrize("route", ["assist", "jobs", "jobs/urls"])
@pytest.mark.parametrize("case", ["overflow", "duplicate_id", "duplicate_index", "non_http",
                                  "invalid_port", "credentials"])
def test_all_review_entrypoints_reject_invalid_image_lists(client, route, case):
    images = [{"index": i, "image_id": f"img-{i}", "src": f"https://images.example.test/{i}.png"}
              for i in range(17 if case == "overflow" else 2)]
    if case == "duplicate_id":
        images[1]["image_id"] = images[0]["image_id"]
    elif case == "duplicate_index":
        images[1]["index"] = images[0]["index"]
    elif case == "non_http":
        images[1]["src"] = "file:///private.png"
    elif case == "invalid_port":
        images[1]["src"] = "https://images.example.test:invalid/a.png"
    elif case == "credentials":
        images[1]["src"] = "https://user:secret@images.example.test/a.png"
    response = client.post(f"/api/review/{route}", json={
        "page_url": "https://example.test/review", "region": "qingdao", "images": images,
    })
    assert response.status_code == 422


def test_health_endpoint(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_review_service_does_not_expose_configuration_tooling(client) -> None:
    assert client.get("/api/config/tasks").status_code == 404
    assert client.post("/api/config/pair", json={"code": "123456"}).status_code == 404
    assert client.get("/api/review/definition").status_code == 404
    assert client.get("/api/review/schema").status_code == 200
    catalog = client.get("/api/review/page-catalog")
    assert catalog.status_code == 200
    assert {item["business_type"] for item in catalog.json()["identities"]} == {
        "scrap_replacement", "consistency", "vehicle_source", "transfer",
    }


def test_url_job_api_creates_and_finishes_without_upload(client, monkeypatch):
    from forj_review_assistant.contracts.recognition import AgentBatchResult
    from forj_review_assistant.runtime.jobs.manager import ReviewJobManager

    class Service:
        async def extract_image_async(self, request, image):
            assert "data_url" not in image.model_dump()
            assert image.src == "https://images.example.test/0.jpg"
            return AgentBatchResult(total_count=1, completed_count=1, completed_image_ids=["url-0"])

        async def assist_with_batch_async(self, request, initial_batch):
            from forj_review_assistant.contracts.review import ReviewResponse
            return ReviewResponse(recommendation="REVIEW_REQUIRED", risk_level="MEDIUM", summary="合成测试"), initial_batch

    manager = ReviewJobManager(Service())
    monkeypatch.setattr(client.app.state, "review_jobs", manager)
    created = client.post("/api/review/jobs/urls", json={
        "page_url": "https://example.test/review", "region": "qingdao",
        "images": [{"index": 0, "imageId": "url-0", "src": "https://images.example.test/0.jpg"}],
    })
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert manager.wait(job_id, timeout=2)
    snapshot = client.get(f"/api/review/jobs/{job_id}").json()
    assert snapshot["status"] == "COMPLETED"
    assert snapshot["progress"]["completed_count"] == 1


def test_url_job_api_rejects_browser_only_image(client):
    result = client.post("/api/review/jobs/urls", json={
        "page_url": "https://example.test/review", "region": "qingdao",
        "images": [{"index": 0, "src": "blob:https://example.test/id"}],
    })
    assert result.status_code == 422
    assert "HTTP" in result.json()["detail"]


def test_image_upload_routes_are_removed(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/review/jobs/stream" not in paths
    assert "/api/review/jobs/{job_id}/images" not in paths
    assert "/api/review/jobs/{job_id}/complete" not in paths


def test_assist_returns_review_structure_for_page_data(client) -> None:
    response = client.post(
        "/api/review/assist",
        json={
            "page_url": "https://example.test/review/1",
            "region": "qingdao",
            "application_id": "APP-1",
            "page_fields": {"old_vehicle.vin": "ABC123"},
            "images": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation"] == "REVIEW_REQUIRED"
    assert "comparisons" in body
    assert "qr_checks" in body
    assert body["context_summary"]["image_count"] == 0
    assert len(body["material_completeness"]["checklist"]) == 6
    assert body["material_completeness"]["status"] == "INCOMPLETE"
    assert any(item["check_id"] == "MATERIAL-COMPLETENESS" for item in body["cross_checks"])
    assert all(item["category"] != "MATERIAL" and item["step_id"] != "BUSINESS-MATERIAL-COMPLETENESS"
               for item in body["review_tasks"])
    subject_step = next(
        item
        for item in body["review_tasks"]
        if item["step_id"] == "BUSINESS-AFFILIATION-SUBJECT-001"
    )
    assert "subject_requirements" in subject_step["details"]


def test_job_api_creates_and_polls_review(client) -> None:
    created = client.post(
        "/api/review/jobs",
        json={"page_url": "https://example.test/review/1", "region": "qingdao", "images": []},
    )

    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "RUNNING"

    polled = client.get(f"/api/review/jobs/{body['job_id']}")
    assert polled.status_code == 200
    assert polled.json()["status"] in {"RUNNING", "PARTIAL", "COMPLETED"}


def test_url_job_api_can_cancel_a_running_job(client, monkeypatch) -> None:
    from forj_review_assistant.runtime.jobs.manager import ReviewJobManager

    started, stopped = threading.Event(), threading.Event()

    class Service:
        async def assist_with_batch_async(self, request, initial_batch):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    monkeypatch.setattr(client.app.state, "review_jobs", ReviewJobManager(Service()))
    created = client.post(
        "/api/review/jobs/urls",
        json={"page_url": "https://example.test/review/cancel", "region": "qingdao", "images": []},
    )
    assert started.wait(2)
    cancelled = client.delete(f"/api/review/jobs/{created.json()['job_id']}")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert stopped.wait(2)
    assert cancelled.json()["phase"] == "CANCELLED"


def test_assist_marks_page_fields_for_manual_review_until_images_are_recognized(client) -> None:
    response = client.post(
        "/api/review/assist",
        json={
            "page_url": "https://example.test/review/1",
            "region": "qingdao",
            "page_fields": {"old_vehicle.vin": "ABC123"},
            "images": [],
        },
    )

    comparison = next(item for item in response.json()["comparisons"] if item["field"] == "old_vehicle.vin")
    assert comparison["status"] == "REVIEW_REQUIRED"


def test_review_service_reports_context_image_summary_and_collection_errors(client) -> None:
    service = ReviewService()
    result = service.assist(
        ReviewRequest(
            page_url="https://example.test/review/1",
            region="qingdao",
            images=[
                {
                    "index": 0,
                    "src": "https://images.example.test/synthetic.jpg",
                    "imageId": "img-0001",
                    "mimeType": "image/jpeg",
                    "sizeBytes": 2,
                    "collectionError": "图片读取失败",
                }
            ],
        )
    )

    assert result.context_summary == {
        "field_count": 0,
        "image_count": 1,
        "image_failed_count": 1,
        "completed_count": 0,
        "failed_count": 1,
        "timed_out_count": 0,
    }
    assert "图片 img-0001 采集失败：图片读取失败" in result.issues


def test_review_service_only_compares_configured_business_fields(client) -> None:
    service = ReviewService()
    result = service.assist(
        ReviewRequest(
            page_url="https://example.test/review/1",
            region="qingdao",
            page_fields={
                "old_vehicle.vin": "OLD-VIN",
                "old_vehicle.engine_model": "ENGINE-1",
                "invoice.code": "INVOICE-CODE",
                "invoice.invoice_no": "INVOICE-NO",
                "invoice.amount": "152000",
                "scrap_certificate.certificate_no": "CERTIFICATE-001",
            },
            images=[],
        )
    )

    compared_fields = {comparison.field for comparison in result.comparisons}
    assert "old_vehicle.vin" in compared_fields
    assert "old_vehicle.engine_model" in compared_fields
    assert "invoice.code" in compared_fields
    assert "invoice.invoice_no" in compared_fields
    assert "invoice.amount" in compared_fields
    assert "scrap_certificate.certificate_no" in compared_fields


def test_review_service_includes_missing_required_business_fields(client) -> None:
    result = ReviewService().assist(
        ReviewRequest(page_url="https://example.test/review/1", region="qingdao", page_fields={}, images=[])
    )

    compared_fields = {comparison.field for comparison in result.comparisons}
    assert "old_vehicle.recycle_date" in compared_fields
    assert "new_vehicle.owner" in compared_fields


def test_collect_manifest_endpoint_serves_fields_groups_and_materials(client) -> None:
    """采集清单接口下发前端做字段匹配和图片归组所需的全部数据。"""
    response = client.get("/api/review/collect-manifest", params={"business_type": "scrap_replacement"})

    assert response.status_code == 200
    body = response.json()
    assert body["business_type"] == "scrap_replacement"

    fields = {item["key"]: item for item in body["fields"]}
    assert fields["old_vehicle.vin"]["aliases"] == ["报废车辆车架号", "旧车车架号", "车架号"]
    assert fields["old_vehicle.vin"]["section"] == "old_vehicle"
    assert fields["old_vehicle.vin"]["section_required"] is True
    assert fields["application.submitted_at"]["reviewable"] is False
    # 挂靠是写回目标，不在页面上采集，因此不出现在清单里。
    assert "old_vehicle.affiliation" not in fields

    groups = {item["label"]: item["scope"] for item in body["page_groups"]}
    assert groups["报废车辆资料"] == "old_vehicle"
    assert groups["新车及发票信息"] == "new_vehicle"
    assert groups["其他图片"] == "other"

    materials = {item["document_type"]: item for item in body["materials"]}
    assert "回收证明" in materials["scrap_certificate"]["hints"]
    assert materials["identity_card"]["label"] == "居民身份证"


def test_collect_manifest_resolves_page_identity_even_without_region_or_version(client) -> None:
    response = client.get(
        "/api/review/collect-manifest",
        params={"business_type": "scrap_replacement", "page_id": "scrap_replacement:qingdao"},
    )

    assert response.status_code == 200
    assert response.json()["region"] == "qingdao"
    assert response.json()["profile_version"] == "1.0"


def test_collect_manifest_is_not_available_for_undeclared_businesses(client) -> None:
    response = client.get("/api/review/collect-manifest", params={"business_type": "consistency"})

    assert response.status_code == 404




def test_configure_log_file_is_noop_without_env(client, monkeypatch) -> None:
    """环境变量和 `.env` 都没配时，行为必须与现状完全一致：只输出控制台。

    只 `delenv` 不够——开发机的 `.env` 里通常配了 `REVIEW_LOG_DIR`，
    `configure_log_file` 会先加载它再读值。所以这里同时把加载动作空掉，
    测的是"哪儿都没配"这一种真实情形。
    """
    monkeypatch.delenv("REVIEW_LOG_DIR", raising=False)
    monkeypatch.setattr("forj_review_assistant.observability.logging.load_dotenv", lambda *_a, **_k: None)
    target = logging.getLogger("uvicorn.error.test_noop")
    configure_log_file(target)
    assert target.handlers == []


def test_configure_log_file_loads_env_file_before_reading_it(client, tmp_path, monkeypatch) -> None:
    """`.env` 里配的 REVIEW_LOG_DIR 必须生效。

    装配日志发生在 `ReviewService()` 之前，而 dotenv 原本只在那里面加载；不先
    加载环境文件，写在 `.env` 里的配置就会静默失效——本地调试最常踩的就是这种
    "配了没反应，但也不报错"。
    """
    monkeypatch.delenv("REVIEW_LOG_DIR", raising=False)

    def fake_load_dotenv(*_args, **_kwargs) -> None:
        os.environ["REVIEW_LOG_DIR"] = str(tmp_path)

    monkeypatch.setattr("forj_review_assistant.observability.logging.load_dotenv", fake_load_dotenv)
    target = logging.getLogger("uvicorn.error.test_dotenv_first")
    try:
        configure_log_file(target)
        assert len(target.handlers) == 1
    finally:
        os.environ.pop("REVIEW_LOG_DIR", None)
        for handler in list(target.handlers):
            handler.close()
            target.removeHandler(handler)


def test_configure_log_file_writes_daily_rotated_file(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REVIEW_LOG_DIR", str(tmp_path))
    target = logging.getLogger("uvicorn.error.test_file")
    # 生产里 uvicorn 的 dictConfig 把 uvicorn.error 设成 INFO；测试环境没有
    # 那份配置，不显式设级别的话 INFO 会在到达文件 handler 之前就被过滤掉。
    target.setLevel(logging.INFO)
    configure_log_file(target)
    try:
        assert len(target.handlers) == 1
        target.info("review log file probe")
        target.handlers[0].flush()
        written = list(tmp_path.glob("review-agent.log*"))
        assert len(written) == 1
        assert "review log file probe" in written[0].read_text(encoding="utf-8")
        # 重复调用不得叠加处理器：否则同一条日志会写两遍。
        configure_log_file(target)
        assert len(target.handlers) == 1
    finally:
        for handler in list(target.handlers):
            handler.close()
            target.removeHandler(handler)


def test_configure_log_file_degrades_when_dir_cannot_be_created(
    client,
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    """容器根文件系统只读时不能让审核服务起不来：降级成一条告警。

    用「已存在的文件」当目录前缀，makedirs 在 Windows 和 Linux 上都会抛
    OSError 的子类，避免依赖某个平台特有的不可写路径。
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("REVIEW_LOG_DIR", str(blocker / "nested"))
    target = logging.getLogger("uvicorn.error.test_unwritable")
    with caplog.at_level("WARNING", logger="uvicorn.error.test_unwritable"):
        configure_log_file(target)
    assert target.handlers == []
    assert any(
        "Review log file disabled" in record.getMessage() for record in caplog.records
    )
