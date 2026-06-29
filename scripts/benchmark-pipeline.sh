#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# benchmark-pipeline.sh — Performance benchmark for deepresearch pipeline
#
# Measures:
#   - Round 1 + web search time
#   - Scribe compilation time
#   - Total session time
#
# Usage:
#   ./scripts/benchmark-pipeline.sh [--quick|--medium|--deep] [--model MODEL]
#   ./scripts/benchmark-pipeline.sh --list        # List recent benchmark results
#   ./scripts/benchmark-pipeline.sh --help        # Show usage
# ──────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BENCHMARK_DIR="$WORKSPACE_DIR/output/benchmarks"
BENCHMARK_LOG="$BENCHMARK_DIR/results.log"

# ── Detect venv ──────────────────────────────────────────────────────────
VENV_PYTHON=""
for candidate in "$WORKSPACE_DIR/.venv/bin/python" "$WORKSPACE_DIR/../.venv/bin/python" "$(which python3)"; do
  if [ -x "$candidate" ]; then
    VENV_PYTHON="$candidate"
    break
  fi
done

if [ -z "$VENV_PYTHON" ]; then
  echo "ERROR: No Python interpreter found" >&2
  exit 1
fi

# ── Help / list mode ─────────────────────────────────────────────────────
if [ "${1:-}" = "--help" ]; then
  sed -n '2,13p' "$0"
  exit 0
fi

if [ "${1:-}" = "--list" ]; then
  if [ -f "$BENCHMARK_LOG" ]; then
    echo "=== Recent Benchmark Results ==="
    column -t -s '|' "$BENCHMARK_LOG" 2>/dev/null || cat "$BENCHMARK_LOG"
  else
    echo "No benchmark results found at $BENCHMARK_LOG"
    echo "Run './scripts/benchmark-pipeline.sh' first."
  fi
  exit 0
fi

# ── Parse arguments ──────────────────────────────────────────────────────
MODE="--quick"
MODEL=""
TOPIC="Benchmark test $(date '+%Y-%m-%d %H:%M')"

while [ $# -gt 0 ]; do
  case "$1" in
    --quick|--medium|--deep) MODE="$1"; shift ;;
    --model) MODEL="--model $2"; shift 2 ;;
    --topic) TOPIC="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Ensure output directory ──────────────────────────────────────────────
mkdir -p "$BENCHMARK_DIR"

# ── Run benchmark ────────────────────────────────────────────────────────
echo "=== DeepResearch Pipeline Benchmark ==="
echo "Mode:    ${MODE#--}"
echo "Topic:   $TOPIC"
echo "Python:  $VENV_PYTHON"
echo ""

export PYTHONPATH="$WORKSPACE_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

# Run with --benchmark flag and capture timing output
BENCHMARK_OUTPUT="$BENCHMARK_DIR/benchmark-$(date '+%Y%m%d-%H%M%S').txt"

START_TIME=$(date +%s.%N)
$VENV_PYTHON -m deepresearch.main run "$TOPIC" $MODE $MODEL --benchmark --dry-run 2>&1 | tee "$BENCHMARK_OUTPUT"
EXIT_CODE=$?
END_TIME=$(date +%s.%N)
TOTAL_TIME=$(echo "$END_TIME - $START_TIME" | bc)

echo ""
echo "=== Results ==="
echo "Exit code:  $EXIT_CODE"
echo "Total time: $(printf '%.2f' "$TOTAL_TIME")s"

# Extract phase timing from output
echo ""
echo "Phase timing:"
grep -E '^\s+\[cyan\].*\[/cyan\]' "$BENCHMARK_OUTPUT" 2>/dev/null || \
  sed -n '/Benchmark Results/,/Total:/p' "$BENCHMARK_OUTPUT" 2>/dev/null || \
  echo "  (no phase timing recorded)"

# Extract round_1 and scribe if available
ROUND1_TIME=$(grep -oP 'round_1:\s+\K[\d.]+' "$BENCHMARK_OUTPUT" 2>/dev/null || echo "N/A")
SCRIBE_TIME=$(grep -oP 'scribe_compilation:\s+\K[\d.]+' "$BENCHMARK_OUTPUT" 2>/dev/null || echo "N/A")

# ── Log results ──────────────────────────────────────────────────────────
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "$TIMESTAMP | ${MODE#--} | ${MODEL:-default} | ${ROUND1_TIME:-N/A} | ${SCRIBE_TIME:-N/A} | $(printf '%.2f' "$TOTAL_TIME")s" >> "$BENCHMARK_LOG"

echo ""
echo "=== Summary logged ==="
echo "Timestamp | Mode | Model | Round 1 (s) | Scribe (s) | Total"
echo "$TIMESTAMP | ${MODE#--} | ${MODEL:-default} | ${ROUND1_TIME:-N/A} | ${SCRIBE_TIME:-N/A} | $(printf '%.2f' "$TOTAL_TIME")s"
echo ""
echo "Full output: $BENCHMARK_OUTPUT"
echo "History:     $BENCHMARK_LOG"

# ── Log file check ───────────────────────────────────────────────────────
echo ""
echo "=== Log File Check ==="
LOG_DIR="$WORKSPACE_DIR/logs"
if [ -d "$LOG_DIR" ]; then
  LOG_SIZE=$(du -sh "$LOG_DIR/deepresearch.log" 2>/dev/null | cut -f1 || echo "N/A")
  SESSION_COUNT=$(find "$LOG_DIR" -name 'session-*.log' 2>/dev/null | wc -l)
  echo "deepresearch.log: $LOG_SIZE"
  echo "Session logs:     $SESSION_COUNT files"
else
  echo "Log directory not found: $LOG_DIR"
fi

exit $EXIT_CODE
