#!/usr/bin/env bash
#
# The nightly soak (docs/plan-test-setup.md §5): the property tests and
# fuzzers on a large budget, against the last commit on `main`, in a
# worktree of its own with its own databases (mor_soak, mor_soak_migrations)
# and its own .hypothesis/. A failure sends a Telegram message.
#
#   deploy/soak.sh               run it now (what the timer runs)
#   deploy/soak.sh install       install and start the systemd user timer
#   deploy/soak.sh test-alert    send one test message to the alerts chat
#
# Each target gets an example budget, so pytest ends by itself and reports
# with a clean exit code; it stops at its first failure (-x). The time
# limit is only a guard against a hang. (Ending targets by time lost a
# found failure once, and a Ctrl-C did not reliably stop the fuzzers,
# 17-Nov-2026.) Judged by its log: any FAILED line is a failure, else exit
# 0 is a pass, else the run broke (a hang guard hit included). Failures and
# broken runs alert.
#
# Needs in the repo's .env: TELEGRAM_BOT_API_TOKEN, TELEGRAM_CHAT_ID_ALERTS.
#
# For checking the soak itself: SOAK_QUICK=1 gives every target 50
# examples; SOAK_DRY_ALERT=1 prints the alert instead of sending it;
# SOAK_REF=<commit> tests that commit instead of `main`.
set -euo pipefail

# A user unit starts with a minimal PATH; uv lives in ~/.local/bin.
export PATH="${HOME}/.local/bin:${PATH}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The worktree the soak tests in, and its logs: outside the repo.
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/mor-soak"
CHECKOUT="${STATE}/checkout"
LOGS="${STATE}/logs"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# Targets: a name, its example budget (per test), its hang guard, and the
# tests it runs. Plain data. Budgets measured on the laptop: echo, probe
# and properties about 30, 10 and 5 minutes (17-Nov-2026); the duty and
# shipping-price fuzzers about 0.11 s an example, so about 18 minutes each,
# and the quote properties about 1 ms an example, which brings properties
# to about 12 minutes (19-Nov-2026); the API properties about 1 ms an
# example too, about 3 minutes more (19-Nov-2026). The quote-flow fuzzer
# about 0.17 s an example, so about 17 minutes (19-Nov-2026). Every test file using Hypothesis must
# be in a target: tests/test_soak_targets.py fails otherwise.
TARGETS=(
  "echo-flow|6000|75m|tests/flow/test_echo_flow_fuzz.py"
  "probe-flow|8000|30m|tests/flow/test_probe_flow.py"
  "duty-flow|10000|45m|tests/flow/test_duty_lookup_fuzz.py"
  "shipping-price-flow|10000|45m|tests/flow/test_shipping_price_fuzz.py"
  "quote-flow|6000|45m|tests/flow/test_quote_flow_fuzz.py"
  "properties|100000|30m|tests/money tests/echo/test_events.py tests/messaging/test_facts.py tests/markets/test_markets.py tests/tax/test_tax_paths.py tests/tax/test_duty_modes.py tests/quote/test_quote_properties.py tests/api/test_api_properties.py tests/checkout/test_addresses.py"
)

env_value() {
  # One value from the repo's .env, without sourcing the file.
  local key="$1"
  grep -E "^${key}=" "${REPO}/.env" 2>/dev/null | head -1 | cut -d= -f2- || true
}

send_alert() {
  local text="$1"
  local token chat
  if [ "${SOAK_DRY_ALERT:-}" = "1" ]; then
    echo "--- alert (not sent) ---"
    echo "$text"
    return 0
  fi
  token="$(env_value TELEGRAM_BOT_API_TOKEN)"
  chat="$(env_value TELEGRAM_CHAT_ID_ALERTS)"
  if [ -z "$token" ] || [ -z "$chat" ]; then
    echo "ALERT NOT SENT: TELEGRAM_BOT_API_TOKEN or TELEGRAM_CHAT_ID_ALERTS missing from .env" >&2
    echo "$text" >&2
    return 1
  fi
  # The token goes in the URL; curl's own messages never print it.
  if ! curl -sS --fail --max-time 20 -o /dev/null \
      "https://api.telegram.org/bot${token}/sendMessage" \
      --data-urlencode "chat_id=${chat}" \
      --data-urlencode "text=${text}"; then
    echo "ALERT NOT SENT: Telegram refused or did not answer" >&2
    echo "$text" >&2
    return 1
  fi
}

prepare_checkout() {
  mkdir -p "$STATE" "$LOGS"
  if [ ! -d "$CHECKOUT" ]; then
    git -C "$REPO" worktree add --detach "$CHECKOUT" "${SOAK_REF:-main}" >/dev/null
  fi
  git -C "$CHECKOUT" checkout --quiet --detach "${SOAK_REF:-main}"
  (cd "$CHECKOUT" && uv sync --quiet)
}

run_soak() {
  prepare_checkout
  local commit stamp failures=()
  commit="$(git -C "$CHECKOUT" rev-parse --short HEAD)"
  stamp="$(date -u +%Y-%m-%dT%H%M%SZ)"
  for target in "${TARGETS[@]}"; do
    IFS='|' read -r name examples guard paths <<<"$target"
    if [ "${SOAK_QUICK:-}" = "1" ]; then
      examples=50
    fi
    local log="${LOGS}/${stamp}-${name}.log"
    local code=0
    # shellcheck disable=SC2086  # paths is a list of words on purpose
    (cd "$CHECKOUT" && MOR_TEST_DATABASE=mor_soak HYPOTHESIS_PROFILE=soak \
      SOAK_EXAMPLES="$examples" timeout "$guard" \
      uv run pytest -x -q -rf -p no:cacheprovider $paths) >"$log" 2>&1 || code=$?
    local outcome="broken"
    if grep -q '^FAILED ' "$log"; then
      outcome="failed"
    elif [ "$code" = 0 ]; then
      outcome="pass"
    fi
    case "$outcome" in
      pass) echo "${name}: pass (exit ${code}), log ${log}" ;;
      failed)
        echo "${name}: FAILED, log ${log}"
        failures+=("${name} found a failure in $(grep -m1 '^FAILED ' "$log" | cut -d' ' -f2 | cut -c1-200 || true)
$(grep -m1 '^E ' "$log" | sed 's/^E *//' | cut -c1-300 || true)
log: ${log}")
        ;;
      *)
        echo "${name}: BROKEN (exit ${code}), log ${log}"
        failures+=("${name} did not run properly (exit ${code}): $(tail -1 "$log" | cut -c1-300 || true)
log: ${log}")
        ;;
    esac
  done
  if [ "${#failures[@]}" -gt 0 ]; then
    local text="MoR soak on ${commit}:"
    for failure in "${failures[@]}"; do
      text="${text}
- ${failure}"
    done
    send_alert "$text"
    return 1
  fi
}

install_timer() {
  mkdir -p "$UNIT_DIR"
  sed "s|@REPO@|${REPO}|g" "${REPO}/deploy/mor-soak.service" >"${UNIT_DIR}/mor-soak.service"
  cp "${REPO}/deploy/mor-soak.timer" "${UNIT_DIR}/mor-soak.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now mor-soak.timer
  systemctl --user list-timers mor-soak.timer --no-pager
}

case "${1:-run}" in
  run) run_soak ;;
  install) install_timer ;;
  test-alert) send_alert "MoR soak: test message from $(hostname). Alerts reach this chat." && echo "sent" ;;
  *)
    echo "usage: deploy/soak.sh [run|install|test-alert]" >&2
    exit 2
    ;;
esac
