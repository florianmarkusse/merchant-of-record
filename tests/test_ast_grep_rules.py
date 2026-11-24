"""The ast-grep rules (sgconfig.yml, ast-grep/rules/) for what ruff cannot
say (AGENTS.md, Checks): the code breaks none, and each
rule's examples in ast-grep/rule-tests/ hold, so a rule that stopped
matching what it is for fails here too."""

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def _ast_grep(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ast-grep", *args], cwd=REPO, capture_output=True, text=True, check=False
    )


def test_the_code_breaks_no_ast_grep_rule() -> None:
    scan = _ast_grep("scan", "--error")
    assert scan.returncode == 0, scan.stdout + scan.stderr


def test_every_ast_grep_rule_catches_its_examples() -> None:
    tested = _ast_grep("test", "--skip-snapshot-tests")
    assert tested.returncode == 0, tested.stdout + tested.stderr
