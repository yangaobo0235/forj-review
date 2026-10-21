"""The documented IDE module must run its parser, rather than exit silently."""

import subprocess
import sys


def test_review_module_exposes_cli_without_starting_server():
    result = subprocess.run(
        [sys.executable, "-m", "forj_review_service.interfaces.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert "usage: forj-review-service" in result.stdout
    assert "--port" in result.stdout
