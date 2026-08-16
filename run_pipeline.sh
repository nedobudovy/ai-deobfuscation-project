#!/usr/bin/env bash
# run_pipeline.sh — one-command end-to-end driver for the study pipeline.
#
#   stage 1  obfuscate    source-level obfuscation  → obfuscated_source_files/
#   stage 2  build        16 OLLVM variants          → build_*/
#   stage 3  decompile    IDA Pro Hex-Rays           → decompiled/{variant}/
#   stage 4  pipeline     LLM deobfuscation          → results/{run_id}/
#   stage 5  evaluate     F+S+R rubric               → eval CSV + summary
#   stage 6  stats        token/latency statistics
#
# Usage:
#   ./run_pipeline.sh                 # run everything
#   ./run_pipeline.sh build           # run a single stage
#   ./run_pipeline.sh 1 2 3           # run selected stages
#   SKIP_STAGES="3 4" ./run_pipeline.sh
#
# Stage 4 options are forwarded to pipeline.py, e.g.:
#   ./run_pipeline.sh pipeline --models x-ai/grok-4.3 --prompts few_shot
#
# Requires: config.yaml present (cp config.yaml.example config.yaml), and for
# stage 4 at least one model enabled.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RUN_NAME="${RUN_NAME:-}"                     # --run-id for the LLM run
EVAL_CSV="${EVAL_CSV:-eval_fsr.csv}"         # F+S+R results file
STATS_CSV="${STATS_CSV:-stats.csv}"          # token/latency export
PIPELINE_ARGS=("${@:2}")

log() { printf '\n════════════════════════════════════════\n  %s\n════════════════════════════════════════\n' "$*"; }

stage_obfuscate() {
    log "Stage 1/6 — source-level obfuscation (obfusCate)"
    ./obfuscate_source_code.sh
}

stage_build() {
    log "Stage 2/6 — building 16 OLLVM variants"
    ./build_variants.sh
}

stage_decompile() {
    log "Stage 3/6 — IDA Pro batch decompilation"
    ./decompile_all.sh
}

stage_pipeline() {
    log "Stage 4/6 — LLM deobfuscation pipeline"
    if [ ! -f config.yaml ]; then
        echo "ERROR: config.yaml missing — copy config.yaml.example to config.yaml and add API keys."
        exit 1
    fi
    local args=()
    [ -n "$RUN_NAME" ] && args+=(--run-id "$RUN_NAME")
    args+=("${PIPELINE_ARGS[@]}")
    python3 pipeline.py "${args[@]}"
}

stage_evaluate() {
    log "Stage 5/6 — automated F+S+R evaluation"
    local run
    run="$([ -n "$RUN_NAME" ] && echo "$RUN_NAME" || echo latest)"
    python3 batch_eval.py --run "$run" --csv "$EVAL_CSV"
}

stage_stats() {
    log "Stage 6/6 — token / latency statistics"
    local run
    run="$([ -n "$RUN_NAME" ] && echo "$RUN_NAME" || echo latest)"
    python3 stats.py --run "$run" --csv "$STATS_CSV"
}

STAGES=${STAGES:-}
if [ -n "$STAGES" ]; then
    SELECTED=($STAGES)
elif [ $# -eq 0 ] || [ $# -ge 1 ] && [[ "$1" =~ ^[0-9]+$ ]] && [ $# -le 2 ] && [[ "${2:-}" =~ ^[0-9]+$ ]]; then
    if [ $# -eq 0 ]; then
        SELECTED=(1 2 3 4 5 6)
    else
        SELECTED=("$@")
    fi
else
    case "$1" in
        obfuscate)  SELECTED=(1) ;;
        build)      SELECTED=(2) ;;
        decompile)  SELECTED=(3) ;;
        pipeline)   SELECTED=(4) ;;
        evaluate)   SELECTED=(5) ;;
        stats)      SELECTED=(6) ;;
        help|-h|--help)
            grep -E "^#   stage|^# Usage|^#   \./run" "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "Unknown stage: $1 (use: obfuscate build decompile pipeline evaluate stats, or a list of stage numbers)"
            exit 1 ;;
    esac
fi

for s in "${SELECTED[@]}"; do
    case "$s" in
        1) stage_obfuscate ;;
        2) stage_build ;;
        3) stage_decompile ;;
        4) stage_pipeline ;;
        5) stage_evaluate ;;
        6) stage_stats ;;
        *) echo "Unknown stage: $s" ;;
    esac
done

echo
echo "Pipeline finished."
echo "  Results : results/"
echo "  F+S+R   : $EVAL_CSV"
echo "  Stats   : $STATS_CSV"
