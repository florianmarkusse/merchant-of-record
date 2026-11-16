"""Start a probe and wait for the worker to finish it: the deploy check.

    uv run python -m mor.probe          # DATABASE_URL, default mor_dev

Exit 0 when the probe reaches `finished`, 1 if it has not after the wait.
The wait only gives up waiting; it decides nothing.
"""

import asyncio
import os
import sys
from datetime import UTC, datetime

from mor.audit import Actor, ActorKind
from mor.config import load_settings
from mor.db.connect import connect
from mor.probe.flow import ProbeState, probe_state, start_probe


WAIT_SECONDS = 30
POLL_SECONDS = 0.2


async def _main() -> int:
    settings = load_settings(os.environ)
    async with await connect(settings.database_url) as conn:
        probe_id = await start_probe(
            conn, Actor(ActorKind.SYSTEM, "probe_cli"), datetime.now(UTC)
        )
        await conn.commit()
        print(f"started probe {probe_id}")
        state: ProbeState | None = None
        for _ in range(int(WAIT_SECONDS / POLL_SECONDS)):
            state = await probe_state(conn, probe_id)
            await conn.commit()
            if state == ProbeState.FINISHED:
                print("finished: the worker is running")
                return 0
            await asyncio.sleep(POLL_SECONDS)
    print(f"not finished after {WAIT_SECONDS}s (state {state}): is the worker running?")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
