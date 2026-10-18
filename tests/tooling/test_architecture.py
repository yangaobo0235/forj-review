import pytest

from scripts.ci.check_architecture import check_architecture


def test_architecture_rejects_reverse_dependencies_and_retired_imports(tmp_path):
    (tmp_path / "contracts").mkdir()
    (tmp_path / "runtime").mkdir()
    (tmp_path / "contracts/review.py").write_text(
        "from forj_review_assistant.runtime import jobs\n"
        "from forj_review_assistant import runtime\n"
        "from .. import integrations\n",
        encoding="utf-8",
    )
    (tmp_path / "runtime/work.py").write_text(
        "from forj_review_assistant.interfaces.http import review\nimport app.main\n",
        encoding="utf-8",
    )
    findings = check_architecture(tmp_path)
    assert len(findings) == 5
    assert any("foundation module" in finding for finding in findings)
    assert any("composition root" in finding for finding in findings)
    assert any("retired package" in finding for finding in findings)


def test_architecture_allows_protocols_and_annotations_only_imports(tmp_path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/work.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "from forj_review_assistant.contracts import review\n"
        "if TYPE_CHECKING:\n"
        "    from forj_review_assistant.bootstrap import application\n",
        encoding="utf-8",
    )
    assert check_architecture(tmp_path) == []


def test_workflow_cannot_register_concrete_business_rules(tmp_path):
    (tmp_path / "runtime/workflow").mkdir(parents=True)
    graph = tmp_path / "runtime/workflow/graph.py"
    graph.write_text("from forj_review_assistant.businesses.rules.vehicle_model import run_vehicle_model\n", encoding="utf-8")
    assert any("concrete business rule" in item for item in check_architecture(tmp_path))
    graph.write_text("from forj_review_assistant.businesses.rules.catalog import build_rule_handlers\n", encoding="utf-8")
    assert check_architecture(tmp_path) == []


def test_configuration_cannot_execute_generated_python(tmp_path):
    (tmp_path / "businesses/configuration").mkdir(parents=True)
    (tmp_path / "businesses/configuration/loader.py").write_text("exec('print(1)')\n", encoding="utf-8")
    assert any("dynamic code" in item for item in check_architecture(tmp_path))


def test_field_decisions_cannot_depend_on_response_rendering(tmp_path):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/comparison.py").write_text(
        "from forj_review_assistant.presentation.response import build_review_response\n",
        encoding="utf-8",
    )
    assert any("API response rendering" in item for item in check_architecture(tmp_path))


def test_graph_cannot_embed_builtin_registration_details(tmp_path):
    (tmp_path / "runtime/workflow").mkdir(parents=True)
    (tmp_path / "runtime/workflow/graph.py").write_text(
        'handlers = {"scrap_certificate_qr": execute}\n', encoding="utf-8",
    )
    assert any("business registration" in item for item in check_architecture(tmp_path))


@pytest.mark.parametrize(("path", "dependency", "reason"), [
    ("runtime/workflow/capability_execution.py", "runtime.workflow.graph.ReviewWorkflow", "imports graph"),
    ("runtime/workflow/state.py", "presentation.routing.build_review_tasks", "imports graph"),
    ("presentation/task_factory.py", "businesses.profiles.BusinessProfile", "task factory"),
    ("runtime/jobs/progress.py", "runtime.jobs.manager.ReviewJobManager", "progress view"),
    ("runtime/workflow/capability_execution.py", "businesses.rules.affiliation.run_affiliation_subject", "concrete business rule"),
])
def test_refactored_modules_cannot_reintroduce_reverse_dependencies(tmp_path, path, dependency, reason):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    module, symbol = dependency.rsplit(".", 1)
    target.write_text(f"from forj_review_assistant.{module} import {symbol}\n", encoding="utf-8")
    assert any(reason in item for item in check_architecture(tmp_path))
