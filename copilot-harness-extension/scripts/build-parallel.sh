#!/usr/bin/env bash
# Run the three build steps concurrently. PyInstaller dominates (~30 s–2 min);
# tsc + asset-copy fit inside that window so their cost is effectively free.
#
# Each step streams output into its own log file and is prefixed when tailed
# so interleaved output is still readable. Exit code is the OR of the three —
# any failure fails the whole build, and we surface the matching log tail.
#
# Run from repo root:  bash copilot-harness-extension/scripts/build-parallel.sh
# Or via npm script:   npm run build      (from copilot-harness-extension/)
#                      npm run build:serial   for the sequential fallback
#
# No dependency on npm-run-all or any third-party runner — plain bash & + wait.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT_DIR="$REPO_ROOT/copilot-harness-extension"
LOG_DIR="$EXT_DIR/.build-logs"
mkdir -p "$LOG_DIR"

echo "── Parallel build (server + assets + compile) ──"

run_step() {
    local label="$1" ; shift
    local logfile="$LOG_DIR/${label}.log"
    : > "$logfile"
    local start=$SECONDS
    if "$@" >"$logfile" 2>&1; then
        echo "  ✓ $label  (${SECONDS} - ${start}s → $((SECONDS - start))s)"
        return 0
    else
        local rc=$?
        echo "  ✗ $label  failed (exit $rc) — tail of $logfile:"
        tail -n 40 "$logfile" | sed 's/^/    /'
        return $rc
    fi
}

# Launch all three steps in the background; collect PIDs.
pids=()
run_step "build:server" bash "$EXT_DIR/scripts/build-server.sh" & pids+=($!)
run_step "build:assets" bash "$EXT_DIR/scripts/copy-assets.sh" & pids+=($!)
# Use cd + "-p ." rather than passing $EXT_DIR explicitly: when WSL bash
# invokes Windows tsc.exe, CWD is auto-translated to a Windows path but
# explicit /mnt/c/... arguments are not — Windows tsc would then fail
# TS5058. cd-then-relative dodges this on every host.
run_step "compile"      bash -c "cd '$EXT_DIR' && exec npx tsc -p ." & pids+=($!)

# wait -n would let us fail fast on the first failure, but masks slower
# steps' logs. Waiting on each PID explicitly captures every exit code.
rc=0
for pid in "${pids[@]}"; do
    if ! wait "$pid"; then rc=1; fi
done

if [[ $rc -ne 0 ]]; then
    echo "── Build FAILED (see tails above, full logs in $LOG_DIR/) ──"
    exit $rc
fi

echo "── Build OK ──"
