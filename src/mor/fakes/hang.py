"""How a fake vendor "did the work, then never answered" (the
`timeout_after_work` fault). Served (dev, the sandbox), it really hangs, and
the caller's own timeout ends the wait. In-process test wiring passes a hang
that ends the call at once as a timeout of the network, so a fuzzer never
races a wall clock: a call that merely ran slow once (a stalled event loop)
must not turn into a timeout that its replay does not see (20-Nov-2026 soak,
`FlakyStrategyDefinition`)."""

import asyncio
from collections.abc import Awaitable, Callable


type Hang = Callable[[], Awaitable[None]]

# Longer than any client timeout.
HANG_SECONDS = 60


async def hang_a_minute() -> None:
    await asyncio.sleep(HANG_SECONDS)
