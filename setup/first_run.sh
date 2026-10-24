#!/usr/bin/env bash
# Made with Grok Bot.
# Idempotent first-run for Claudia tools + ledger.
# Creates folders, downloads tools (when network allows), runs ledger init and
# check, builds an empty Brain, and prints a plain-English OK/problem summary.
# Never overwrites existing ledger CSV data on re-run.

set -u

REPO_RAW="${CLAUDIA_REPO_RAW:-https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main}"
TOOLS_DIR="${CLAUDIA_TOOLS_DIR:-/workspace/claudia-tools}"
LEDGER_DIR="${CLAUDIA_LEDGER_DIR:-/workspace/claudia-ledger}"
# When running from a git checkout, prefer local copies over the network.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

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
  say_problem "could not create folders"
  echo
  echo "Summary: ${oks} ok, ${problems} problem(s)."
  exit 1
}
say_ok "folders ready"

# Download / refresh tool files (safe to re-run; tools are replaceable).
tool_files=(
  "VERSION"
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
    if python3 "${LEDGER_PY}" --ledger "${LEDGER_DIR}" init --force; then
      say_ok "ledger already present; filled only missing starter files (data kept)"
    else
      say_problem "ledger init --force failed"
    fi
  else
    if python3 "${LEDGER_PY}" --ledger "${LEDGER_DIR}" init; then
      say_ok "ledger initialized"
    else
      say_problem "ledger init failed"
    fi
  fi

  if python3 "${LEDGER_PY}" --ledger "${LEDGER_DIR}" check; then
    say_ok "ledger check passed"
  else
    say_problem "ledger check failed (see messages above)"
  fi

  BRAIN_TEMPLATE="${TOOLS_DIR}/brain/claudia-brain.canvas.tsx"
  if [[ ! -f "${BRAIN_TEMPLATE}" && -f "${REPO_ROOT}/brain/claudia-brain.canvas.tsx" ]]; then
    BRAIN_TEMPLATE="${REPO_ROOT}/brain/claudia-brain.canvas.tsx"
  fi
  OUT_BRAIN="/tmp/claudia-brain-ready.canvas.tsx"
  if [[ -f "${BRAIN_TEMPLATE}" ]]; then
    if python3 "${LEDGER_PY}" --ledger "${LEDGER_DIR}" build-brain \
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
  echo "Summary: all good (${oks} ok). Claudia is ready."
  echo "Next: open docs/FOR-BOTS.md (or ${TOOLS_DIR}/docs/FOR-BOTS.md) and run the welcome tour."
  exit 0
fi

echo "Summary: ${oks} ok, ${problems} problem(s). Fix the PROBLEM lines, then re-run this script."
echo "Existing ledger data was not overwritten."
exit 1
