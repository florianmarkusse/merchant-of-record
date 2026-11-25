"""The browser smoke suite's setup (docs/plan-payments.md §4, card fields;
docs/plan-architecture.md §10): a test setup of its own, the real server
and worker against `mor_smoke`, on its own port, so it never touches
`mor_sandbox` or `mor_test`. Not part of `uv run pytest` (testpaths is
tests/); run it with `uv run pytest tests_browser`, and nightly beside the
soak.

    the database dropped and made, migrated, the markets loaded
    the server and the worker started; each confirmed bound in its log
    the seed: fake accounts, the Sandbox Shop and its API key
"""

import os
import re
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest


REPO = Path(__file__).resolve().parent.parent
DATABASE = "mor_smoke"
PORT = 8742
SELF_URL = f"http://127.0.0.1:{PORT}"
ENV = {
    **os.environ,
    "ENVIRONMENT": "test",
    "DATABASE_URL": f"postgresql:///{DATABASE}",
    "SELF_URL": SELF_URL,
}
BOUND_WITHIN_SECONDS = 30


@dataclass(frozen=True)
class Smoke:
    self_url: str
    api_key: str


def _run(*args: str) -> str:
    done = subprocess.run(
        ["uv", "run", *args],
        cwd=REPO,
        env=ENV,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, f"{args}: {done.stdout}\n{done.stderr}"
    return done.stdout


def _recreate_database() -> None:
    with psycopg.connect("postgresql:///postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {DATABASE}")


def _wait_for(log: Path, ready: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + BOUND_WITHIN_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"exited early:\n{log.read_text()}")
        if ready in log.read_text():
            return
        time.sleep(0.2)
    raise AssertionError(f"not ready in {BOUND_WITHIN_SECONDS} s:\n{log.read_text()}")


@pytest.fixture(scope="session")
def smoke(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Smoke]:
    logs = tmp_path_factory.mktemp("smoke")
    _recreate_database()
    _run("python", "-m", "mor.db.migrate")
    _run("python", "-m", "mor.fakes.migrate")
    _run("python", "-m", "mor.markets.load", "--write")
    # The pages' scripts and the fake PSP's, from ts/.
    subprocess.run(["npx", "tsc"], cwd=REPO, check=True)
    started: list[tuple[subprocess.Popen[bytes], Path]] = []
    try:
        for name, module, ready in (
            ("server", "mor.web.serve", "Application startup complete"),
            ("worker", "mor.messaging.worker", ""),
        ):
            log = logs / f"{name}.log"
            with log.open("wb") as out:
                process = subprocess.Popen(
                    ["uv", "run", "python", "-m", module],
                    cwd=REPO,
                    env=ENV,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                )
            started.append((process, log))
            _wait_for(log, ready, process)
        seeded = _run("python", "-m", "mor.seed")
        found = re.search(r"API key \(shown once\): (\S+)", seeded)
        assert found is not None, seeded
        yield Smoke(SELF_URL, found.group(1))
    finally:
        for process, _ in started:
            process.terminate()
        for process, _ in started:
            process.wait(timeout=10)
