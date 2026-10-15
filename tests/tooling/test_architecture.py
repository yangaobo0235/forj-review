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
