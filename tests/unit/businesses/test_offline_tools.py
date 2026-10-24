import copy
import json
from pathlib import Path

import pytest

from forj_review_service.businesses.page_config.inspection import compare_configurations
from forj_review_service.interfaces.cli import main
from forj_review_service.runtime.configuration.service import (
    inspect_directory,
    replay_cases,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def page_directory(tmp_path):
    directory = tmp_path / "pages"
    directory.mkdir()
    data = json.loads((EXAMPLES / "permit.json").read_text(encoding="utf-8"))
    (directory / "permit.json").write_text(json.dumps(data), encoding="utf-8")
    return directory


def _edit(directory, **changes):
    path = directory / "permit.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_effective_preview_is_stable_and_diff_tracks_fields_across_versions(
    page_directory, tmp_path
):
    before = inspect_directory(page_directory)
    assert before == inspect_directory(page_directory)
    next_directory = tmp_path / "next"
    next_directory.mkdir()
    data = json.loads((page_directory / "permit.json").read_text(encoding="utf-8"))
    data["version"] = "2.0"
    data["fields"][0]["normalizer"] = "text"
    data["fields"][0]["evidence"][0]["field"] = "printed.new_vin"
    (next_directory / "new.json").write_text(json.dumps(data), encoding="utf-8")
    after = inspect_directory(next_directory)
    change = compare_configurations(before, after)["pages"]["changed"]["example_permit"]
    assert change["before_version"] == "1.0"
    assert change["after_version"] == "2.0"
    assert change["fields"]["changed"]["permit.vin"]["after"]["normalizer"] == "text"
    assert change["materials"]["changed"]["permit"]["after"]["fields"] == ["printed.new_vin"]


@pytest.mark.asyncio
async def test_sample_replay_runs_real_graph_without_network(page_directory, monkeypatch, caplog):
    async def forbidden(*args, **kwargs):
        raise AssertionError("replay must not access the network")

    monkeypatch.setattr("httpx.AsyncClient.request", forbidden)
    caplog.set_level("INFO", logger="forj_review_service.runtime.workflow.graph")
    result = await replay_cases(page_directory, EXAMPLES / "replay/permit-cases.json")
    assert result["ok"]
    assert len(result["cases"]) == 4
    assert {r["actual"]["recommendation"] for r in result["cases"]} == {"PASS", "REVIEW_REQUIRED"}
    assert all(len(r["fingerprint"]) == 64 for r in result["cases"])
    assert any(
        f"configuration_sha256={result['cases'][0]['fingerprint']}" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_external_replay_never_calls_live_qr_even_without_fixtures(
    page_directory, tmp_path, monkeypatch
):
    from forj_review_service.runtime.review_service import ReviewService
    from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO

    async def forbidden(*args, **kwargs):
        raise AssertionError("live QR verification is forbidden")

    monkeypatch.setattr(ReviewService, "_collect_qr_checks", forbidden)
    profile = SCRAP_REPLACEMENT_QINGDAO
    from tests.support.pages import page_definition

    (page_directory / "scrap.json").write_text(
        json.dumps(page_definition(profile)), encoding="utf-8"
    )
    suite = {
        "cases": [
            {
                "name": "本地缺失外部样例必须降级",
                "request": {
                    "page_id": profile.page_id,
                    "business_type": profile.business_type,
                    "region": profile.region,
                    "profile_version": profile.version,
                    "page_url": "https://example.test/synthetic",
                    "images": [
                        {
                            "index": 0,
                            "src": "https://example.test/synthetic.jpg",
                            "business_scope": "old_vehicle",
                            "document_type_hint": "scrap_certificate",
                        }
                    ],
                },
                "batch": {"total_count": 1, "completed_count": 1},
                "expect": {"recommendation": "REVIEW_REQUIRED"},
            }
        ]
    }
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(suite), encoding="utf-8")
    result = await replay_cases(page_directory, path)
    assert result["ok"]
    assert "EXTERNAL-scrap_certificate_qr" in result["cases"][0]["actual"]["checks"]
    assert result["cases"][0]["actual"]["capabilities"]["scrap_certificate_qr"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_generic_page_uses_declared_projector_and_field_write_permissions(tmp_path):
    from forj_review_service.businesses.page_config.compiler import compile_page
    from forj_review_service.businesses.page_config.schema import PageDefinition
    from forj_review_service.businesses.registry import BusinessRegistry
    from forj_review_service.contracts.recognition import RecognitionBatchResult
    from forj_review_service.contracts.review import ReviewRequest
    from forj_review_service.runtime.review_service import ReviewService

    profile = compile_page(
        PageDefinition.model_validate(
            {
                "settings": {},
                "page_id": "custom_page",
                "business_type": "custom_business",
                "title": "自定义类型",
                "paths": ["/custom"],
                "scopes": ["custom"],
                "page_actions": ["fill_review_fields"],
                "writable_control_kinds": ["text"],
                "identity_anchors": ["application.id"],
                "record_identity_fields": ["application.id"],
                "fields": [
                    {
                        "key": "application.owner_type",
                        "label": "自定义类型",
                        "aliases": ["自定义类型"],
                        "mode": "SYSTEM",
                        "writable": False,
                    },
                    {
                        "key": "application.id",
                        "label": "单号",
                        "aliases": ["单号"],
                        "required": False,
                        "reviewable": False,
                    },
                ],
            }
        )
    )
    service = ReviewService(registry=BusinessRegistry((profile,)))
    request = ReviewRequest(
        page_id=profile.page_id,
        business_type=profile.business_type,
        page_url="https://example.test/custom",
        page_fields={"application.id": "SYNTHETIC", "application.owner_type": "自定义类型值"},
        images=[
            {"index": 0, "src": "https://example.test/synthetic.jpg", "business_scope": "custom"}
        ],
        review_fields=[
            {
                "field": "application.owner_type",
                "label": "自定义类型",
                "value": "自定义类型值",
                "control_type": "text",
                "editable": True,
            }
        ],
    )
    response, _ = await service.assist_with_batch_async(
        request,
        initial_batch=RecognitionBatchResult(total_count=1, completed_count=1),
    )
    assert response.recommendation == "PASS"
    task = next(t for t in response.review_tasks if t.page_target_field == "application.owner_type")
    assert task.result_status == "MATCH"
    assert not task.writable  # A page action and editable control cannot override field policy.


@pytest.mark.asyncio
async def test_replay_reports_lost_checks_and_manifest_changes(page_directory, tmp_path):
    suite = json.loads((EXAMPLES / "replay/permit-cases.json").read_text(encoding="utf-8"))
    suite["cases"] = [suite["cases"][0]]
    suite["cases"][0]["expect"]["checks"] = {"removed-rule": "MATCH"}
    suite["cases"][0]["expect"]["manifest_fields"] = ["nonexistent.field"]
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(suite), encoding="utf-8")
    result = await replay_cases(page_directory, cases)
    assert not result["ok"]
    assert {f["path"] for f in result["cases"][0]["failures"]} == {
        "checks.removed-rule",
        "manifest_fields",
    }


@pytest.mark.asyncio
async def test_replay_requires_fixed_batch_and_exact_page(page_directory, tmp_path):
    source = json.loads((EXAMPLES / "replay/permit-cases.json").read_text(encoding="utf-8"))
    for property_name in ("batch", "page_id"):
        suite = copy.deepcopy(source)
        if property_name == "batch":
            del suite["cases"][0]["batch"]
        else:
            del suite["cases"][0]["request"]["page_id"]
        cases = tmp_path / "cases.json"
        cases.write_text(json.dumps(suite), encoding="utf-8")
        with pytest.raises(ValueError):
            await replay_cases(page_directory, cases)


def test_cli_check_returns_json_errors_and_does_not_start_server(
    page_directory, monkeypatch, capsys
):
    _edit(
        page_directory,
        fields=[
            {
                "key": "permit.vin",
                "label": "VIN",
                "locator_id": "vin",
                "evidence": [{"material": "permit", "field": "license.vin"}],
            }
        ],
    )
    monkeypatch.setattr("sys.argv", ["review", "--check-pages", str(page_directory), "--json"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert not result["ok"]
    assert "aliases" in result["errors"][0]["message"]


def test_cli_replay_returns_nonzero_on_regression(page_directory, tmp_path, monkeypatch, capsys):
    suite = json.loads((EXAMPLES / "replay/permit-cases.json").read_text(encoding="utf-8"))
    suite["cases"][0]["expect"]["recommendation"] = "REVIEW_REQUIRED"
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(suite), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "review",
            "--replay-pages",
            str(page_directory),
            "--cases",
            str(cases),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert not json.loads(capsys.readouterr().out)["ok"]


@pytest.mark.asyncio
async def test_replay_checks_why_a_configured_field_needs_review(page_directory, tmp_path):
    data = json.loads((EXAMPLES / "replay/permit-cases.json").read_text(encoding="utf-8"))
    case = data["cases"][0]
    case["batch"]["observations"][0]["uncertain"] = True
    case["expect"].update(
        {
            "recommendation": "REVIEW_REQUIRED",
            "comparisons": {"permit.vin": "REVIEW_REQUIRED"},
            "comparison_reasons": {"permit.vin": ["RECOGNITION_UNCERTAIN"]},
        }
    )
    data["cases"] = [case]
    path = tmp_path / "reason-cases.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert (await replay_cases(page_directory, path))["ok"]
    case["expect"]["comparison_reasons"]["permit.vin"] = ["EVIDENCE_MISSING"]
    path.write_text(json.dumps(data), encoding="utf-8")
    result = await replay_cases(page_directory, path)
    assert not result["ok"]
    assert result["cases"][0]["failures"][0]["path"] == "comparison_reasons.permit.vin"
