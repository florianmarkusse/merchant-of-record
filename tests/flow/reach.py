"""How far a fuzzer's runs go (docs/plan-fuzz-reach.md §3).

Each run records the milestones it reached. `finish` writes the reach
table to `.hypothesis/reach/` (git-ignored; the soak appends it to its
log) and, from `MIN_RUNS` runs on, fails if a milestone was reached by
fewer than `FLOOR` of them: a drop in reach then alerts like a bug.

A tally is a `Counter`: each milestone's runs, and the runs themselves
under `RUNS`. Reach is measured, not steered: `hypothesis.target` on it
was tried and dropped (docs/plan-fuzz-reach.md §4).
"""

from collections import Counter
from pathlib import Path


RUNS = "(runs)"
MIN_RUNS = 500
FLOOR = 1 / 200
REACH_DIR = Path(__file__).resolve().parents[2] / ".hypothesis" / "reach"


def record(tally: Counter[str], reached: set[str]) -> None:
    """Call once per run, from the machine's teardown."""
    tally[RUNS] += 1
    for milestone in reached:
        tally[milestone] += 1


def table(name: str, milestones: tuple[str, ...], tally: Counter[str]) -> str:
    runs = tally[RUNS]
    lines = [f"reach of {name}: {runs} runs"]
    for milestone in milestones:
        count = tally[milestone]
        share = count / runs if runs else 0.0
        lines.append(f"  {milestone:<40} {count:>6} {share:>7.1%}")
    return "\n".join(lines) + "\n"


def finish(name: str, milestones: tuple[str, ...], tally: Counter[str]) -> None:
    text = table(name, milestones, tally)
    REACH_DIR.mkdir(parents=True, exist_ok=True)
    (REACH_DIR / f"{name}.txt").write_text(text)
    runs = tally[RUNS]
    if runs < MIN_RUNS:
        return
    short: list[str] = []
    for milestone in milestones:
        if tally[milestone] < runs * FLOOR:
            short.append(milestone)
    assert not short, f"reached by fewer than 1 run in 200: {short}\n{text}"
