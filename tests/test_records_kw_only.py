"""A record of 4 or more fields is built with keyword arguments (AGENTS.md,
Code Style): two positional values of one type can swap unnoticed. Pyright
refuses a positional build once the record says `kw_only=True`, so every
such record must say it: a frozen dataclass in its decorator, a msgspec
struct in its class keywords."""

import ast
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
MIN_FIELDS = 4


def _field_count(record: ast.ClassDef) -> int:
    count = 0
    for statement in record.body:
        if not isinstance(statement, ast.AnnAssign):
            continue
        annotation = statement.annotation
        if isinstance(annotation, ast.Subscript) and ast.unparse(
            annotation.value
        ).endswith("ClassVar"):
            continue
        count += 1
    return count


def _says_kw_only(keywords: list[ast.keyword]) -> bool:
    for keyword in keywords:
        if keyword.arg == "kw_only":
            return (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is True
            )
    return False


def _is_kw_only_record(record: ast.ClassDef) -> bool | None:
    """True or False for a dataclass or msgspec struct, None for any other
    class."""
    for decorator in record.decorator_list:
        if isinstance(decorator, ast.Call):
            name = ast.unparse(decorator.func)
            keywords = decorator.keywords
        else:
            name = ast.unparse(decorator)
            keywords = []
        if name in ("dataclass", "dataclasses.dataclass"):
            return _says_kw_only(keywords)
    for base in record.bases:
        if ast.unparse(base) in ("msgspec.Struct", "Struct"):
            return _says_kw_only(record.keywords)
    return None


def test_every_record_of_four_or_more_fields_is_keyword_only() -> None:
    missing: list[str] = []
    checked = 0
    for root in ("src", "tests"):
        for file in sorted((REPO / root).rglob("*.py")):
            tree = ast.parse(file.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if _field_count(node) < MIN_FIELDS:
                    continue
                kw_only = _is_kw_only_record(node)
                if kw_only is None:
                    continue
                checked += 1
                if not kw_only:
                    relative = file.relative_to(REPO).as_posix()
                    missing.append(f"{relative}:{node.lineno} {node.name}")
    assert checked > 100, f"found only {checked} records: is the walk broken?"
    assert not missing, "add kw_only=True to:\n" + "\n".join(missing)
