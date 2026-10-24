#!/usr/bin/env bash
# Made with Grok Bot.
# Idempotent first-run for Claudia tools + ledger under $CLAUDIA_HOME.
# Creates folders, downloads tools (when network allows), runs ledger init and
# check, builds an empty Brain, and prints a plain-English OK/problem summary.
# Never overwrites existing ledger CSV data on re-run.
# Refuses to run without CLAUDIA_HOME (or --home) and CLAUDIA_BOT_ID (or --bot-id).

set -u

REPO_RAW="${CLAUDIA_REPO_RAW:-https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main}"
# When running from a git checkout, prefer local copies over the network.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOME_DIR=""
BOT_ID=""

usage() {
  cat <<'EOF'
Usage: first_run.sh [--home DIR] [--bot-id ID]

Requires this bot's own home and bot id (no shared default):
  CLAUDIA_HOME or --home   e.g. /workspace/claudia-<bot-id>
                           or /cursor/stores/<its-store>/claudia
  CLAUDIA_BOT_ID or --bot-id

Flag wins over the env var. Save CLAUDIA_HOME in memory and reuse it every routine.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --home)
      HOME_DIR="${2:-}"
      shift 2
      ;;
    --bot-id)
      BOT_ID="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "PROBLEM: unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${HOME_DIR}" ]]; then
  HOME_DIR="${CLAUDIA_HOME:-}"
fi
if [[ -z "${BOT_ID}" ]]; then
  BOT_ID="${CLAUDIA_BOT_ID:-}"
fi

if [[ -z "${HOME_DIR}" ]]; then
  echo "PROBLEM: CLAUDIA_HOME is not set and --home was not passed."
  echo "Set CLAUDIA_HOME to this bot's own folder, or pass --home."
  echo "Example: export CLAUDIA_HOME=/workspace/claudia-<bot-id>"
  echo "(or /cursor/stores/<its-store>/claudia if the bot has a persistent store)."
  echo "There is no shared default, so two Claudia copies on one machine do not mix ledgers."
  exit 1
fi

if [[ -z "${BOT_ID}" ]]; then
  echo "PROBLEM: CLAUDIA_BOT_ID is not set and --bot-id was not passed."
  echo "Set CLAUDIA_BOT_ID to this bot's id so owner.json records who owns the ledger."
  exit 1
fi

TOOLS_DIR="${HOME_DIR}/tools"
LEDGER_DIR="${HOME_DIR}/ledger"

problems=0
oks=0

say_ok() {
  echo "OK: $*"
  oks=$((oks + 1))
}

say_problem() {
  echo "PROBLEM: $*"
  problems=$((problems + 1))
}

copy_or_curl() {
  # copy_or_curl <repo-relative-path> <dest>
  local rel="$1"
  local dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [[ -f "${REPO_ROOT}/${rel}" ]]; then
    cp -f "${REPO_ROOT}/${rel}" "$dest"
    return 0
  fi
  if command -v curl >/dev/null 2>&1; then
    if curl -fsSL -o "$dest" "${REPO_RAW}/${rel}"; then
      return 0
    fi
  fi
  return 1
}

echo "Claudia first_run"
echo "home:   ${HOME_DIR}"
echo "bot_id: ${BOT_ID}"
echo "tools:  ${TOOLS_DIR}"
echo "ledger: ${LEDGER_DIR}"
echo

mkdir -p "${TOOLS_DIR}" "${LEDGER_DIR}" \
  "${TOOLS_DIR}/ledger/templates" \
  "${TOOLS_DIR}/templates" \
  "${TOOLS_DIR}/scan" \
  "${TOOLS_DIR}/update" \
  "${TOOLS_DIR}/setup" \
  "${TOOLS_DIR}/brain" \
  "${TOOLS_DIR}/docs" || {
  say_problem "could not create folders under ${HOME_DIR}"
  echo
  echo "Summary: ${oks} ok, ${problems} problem(s)."
  exit 1
}
say_ok "folders ready under ${HOME_DIR}"

# Download / refresh tool files (safe to re-run; tools are replaceable).
tool_files=(
  "VERSION"
  "CHANGELOG.md"
  "MANIFEST.sha256"
  "ledger/claudia_ledger.py"
  "brain/claudia-brain.canvas.tsx"
  "templates/claudia_render.py"
  "templates/morning_brief.txt"
  "templates/promise_catch.txt"
  "templates/meditate.txt"
  "templates/weekly_review.txt"
  "templates/welcome_tour.txt"
  "scan/claudia_scan.py"
  "update/claudia_update.py"
  "setup/first_run.sh"
  "docs/FOR-BOTS.md"
)

for rel in "${tool_files[@]}"; do
  if copy_or_curl "$rel" "${TOOLS_DIR}/${rel}"; then
    :
  else
    say_problem "could not install ${rel}"
  fi
done

for f in people organizations projects commitments interactions links claudia_log merges health_history; do
  if copy_or_curl "ledger/templates/${f}.csv" "${TOOLS_DIR}/ledger/templates/${f}.csv"; then
    :
  else
    say_problem "could not install ledger/templates/${f}.csv"
  fi
done

chmod +x \
  "${TOOLS_DIR}/ledger/claudia_ledger.py" \
  "${TOOLS_DIR}/templates/claudia_render.py" \
  "${TOOLS_DIR}/scan/claudia_scan.py" \
  "${TOOLS_DIR}/update/claudia_update.py" \
  "${TOOLS_DIR}/setup/first_run.sh" 2>/dev/null || true

if [[ -f "${TOOLS_DIR}/ledger/claudia_ledger.py" ]]; then
  say_ok "ledger tool installed"
else
  say_problem "ledger tool missing"
fi

if [[ -f "${TOOLS_DIR}/brain/claudia-brain.canvas.tsx" ]]; then
  say_ok "Brain template installed"
else
  say_problem "Brain template missing"
fi

LEDGER_PY="${TOOLS_DIR}/ledger/claudia_ledger.py"
if [[ ! -f "${LEDGER_PY}" && -f "${REPO_ROOT}/ledger/claudia_ledger.py" ]]; then
  LEDGER_PY="${REPO_ROOT}/ledger/claudia_ledger.py"
fi

export CLAUDIA_HOME="${HOME_DIR}"
export CLAUDIA_BOT_ID="${BOT_ID}"

# Ledger init: never wipe existing CSVs. Use --force so missing files are filled.
if [[ -f "${LEDGER_PY}" ]]; then
  existing=0
  for f in people organizations projects commitments interactions links claudia_log merges health_history; do
    if [[ -f "${LEDGER_DIR}/${f}.csv" ]]; then
      existing=1
      break
    fi
  done
  if [[ "$existing" -eq 1 ]]; then
    if python3 "${LEDGER_PY}" --home "${HOME_DIR}" --bot-id "${BOT_ID}" init --force; then
      say_ok "ledger already present; filled only missing starter files (data kept)"
    else
      say_problem "ledger init --force failed (bot_id mismatch or other error)"
    fi
  else
    if python3 "${LEDGER_PY}" --home "${HOME_DIR}" --bot-id "${BOT_ID}" init; then
      say_ok "ledger initialized with bot_id=${BOT_ID}"
    else
      say_problem "ledger init failed"
    fi
  fi

  if python3 "${LEDGER_PY}" --home "${HOME_DIR}" --bot-id "${BOT_ID}" check; then
    say_ok "ledger check passed"
  else
    say_problem "ledger check failed (see messages above; bot_id mismatch is blocking)"
  fi

  BRAIN_TEMPLATE="${TOOLS_DIR}/brain/claudia-brain.canvas.tsx"
  if [[ ! -f "${BRAIN_TEMPLATE}" && -f "${REPO_ROOT}/brain/claudia-brain.canvas.tsx" ]]; then
    BRAIN_TEMPLATE="${REPO_ROOT}/brain/claudia-brain.canvas.tsx"
  fi
  OUT_BRAIN="${HOME_DIR}/claudia-brain-ready.canvas.tsx"
  if [[ -f "${BRAIN_TEMPLATE}" ]]; then
    if python3 "${LEDGER_PY}" --home "${HOME_DIR}" --bot-id "${BOT_ID}" build-brain \
      --template "${BRAIN_TEMPLATE}" \
      -o "${OUT_BRAIN}"; then
      say_ok "empty Brain built at ${OUT_BRAIN}"
    else
      say_problem "build-brain failed"
    fi
  else
    say_problem "no Brain template to build with"
  fi
else
  say_problem "cannot run ledger commands; tool missing"
fi

echo
if [[ "$problems" -eq 0 ]]; then
  echo "Summary: all good (${oks} ok). Claudia is ready for bot_id=${BOT_ID}."
  echo "Remember: export CLAUDIA_HOME=${HOME_DIR} and CLAUDIA_BOT_ID=${BOT_ID} (save in memory)."
  echo "Next: open ${TOOLS_DIR}/docs/FOR-BOTS.md and run the welcome tour."
  exit 0
fi

echo "Summary: ${oks} ok, ${problems} problem(s). Fix the PROBLEM lines, then re-run this script."
echo "Existing ledger data was not overwritten."
exit 1
