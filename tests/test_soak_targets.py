"""The nightly soak (deploy/soak.sh) runs every property test and fuzzer:
a test file that uses Hypothesis but is in no soak target only ever gets
the small budget of `uv run pytest`. Six files were missed this way between
17-Nov-2026 and 19-Nov-2026."""

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
IMPORTS = ("from hypothesis", "import hypothesis")  # at the start of a line


def _soak_paths() -> list[str]:
    """The paths named in soak.sh's TARGETS block, one word each."""
    paths: list[str] = []
    inside = False
    for line in (REPO / "deploy" / "soak.sh").read_text().splitlines():
        stripped = line.strip()
        if stripped == "TARGETS=(":
            inside = True
            continue
        if inside and stripped == ")":
            break
        if inside and stripped.startswith('"'):
            fields = stripped.strip('"').split("|")
            assert len(fields) == 4, f"a target needs four fields: {stripped}"
            for word in fields[3].split():
                paths.append(word)
    return paths


def _covered(path: str, soak_paths: list[str]) -> bool:
    for soak_path in soak_paths:
        if path == soak_path or path.startswith(soak_path.rstrip("/") + "/"):
            return True
    return False


def test_every_test_file_using_hypothesis_is_in_a_soak_target() -> None:
    soak_paths = _soak_paths()
    assert soak_paths, "no TARGETS found in deploy/soak.sh"
    for soak_path in soak_paths:
        assert (REPO / soak_path).exists(), f"soak target {soak_path} does not exist"
    missing: list[str] = []
    for file in sorted((REPO / "tests").rglob("test_*.py")):
        uses = False
        for line in file.read_text().splitlines():
            if line.startswith(IMPORTS):
                uses = True
        relative = file.relative_to(REPO).as_posix()
        if uses and not _covered(relative, soak_paths):
            missing.append(relative)
    assert missing == [], f"add these to a target in deploy/soak.sh: {missing}"
