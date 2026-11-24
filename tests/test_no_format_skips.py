"""The formatter decides every layout (decided 24-Nov-2026): no `fmt: skip`,
`fmt: off` or `fmt: on` comment in `src/` or `tests/`. A hand layout drifts
from the code around it (positional builds kept compact under `fmt: skip`
outgrew the line once their fields were named)."""

import io
import tokenize
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
DIRECTIVES = ("fmt: skip", "fmt: off", "fmt: on", "fmt:skip", "fmt:off", "fmt:on")


def test_no_comment_tells_the_formatter_to_skip() -> None:
    found: list[str] = []
    for root in ("src", "tests"):
        for file in sorted((REPO / root).rglob("*.py")):
            source = file.read_text()
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for token in tokens:
                if token.type != tokenize.COMMENT:
                    continue
                for directive in DIRECTIVES:
                    if directive in token.string:
                        relative = file.relative_to(REPO).as_posix()
                        found.append(f"{relative}:{token.start[0]} {token.string}")
    assert not found, "let ruff format decide the layout:\n" + "\n".join(found)
