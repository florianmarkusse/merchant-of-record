#!/usr/bin/env bash
# This project's checks beyond the shared order (AGENTS.md, Checks), run by
# the Stop hook in ~/Desktop/conventions/hooks/ between pyright and pytest.
set -euo pipefail
uv run vulture
npx tsc
