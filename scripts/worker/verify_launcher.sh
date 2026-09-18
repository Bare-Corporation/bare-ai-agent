#!/usr/bin/env bash
# verify_launcher.sh - deterministic launcher-state check for M2M / SSH callers.
#
# WHY THIS EXISTS
#   The bare() launcher lives in ~/.bashrc, and ~/.bashrc is only read by
#   INTERACTIVE shells. A non-interactive SSH command (the M2M bus, a cron job,
#   an automation caller) therefore cannot simply run 'bare' - the shell reports
#   "command not found" and the check has nothing to verify. Note that the
#   installer does NOT wrap the launcher in a 'case $- in *i*)' guard; the block
#   is written unconditionally. The limitation is purely that a non-interactive
#   shell never sources ~/.bashrc.
#
#   This script is the supported way to verify the launcher without a tty. It
#   reads the managed block straight out of ~/.bashrc, sources it in a subshell,
#   evaluates the prompt-handoff logic with a throwaway prompt, and prints the
#   resulting environment variables.
#
# USAGE
#   verify_launcher.sh [--json] [--quiet] [--home DIR] [--help]
#
# EXIT STATUS
#   0 = every required check passed
#   1 = at least one required check failed (see "failures")
#   2 = usage error
#
# OUTPUT KEYS
#   home, bashrc, bashrc_mtime, block, bare_function, prompt_file_handoff,
#   legacy_prompt_export, run_dir, run_dir_mode, prompt_file,
#   prompt_file_writable, legacy_prompt (set/unset), legacy_prompt_bytes,
#   legacy_prompt_value (only with --show-prompt-value), failures, status

set -uo pipefail

JSON=false
QUIET=false
SHOW_PROMPT_VALUE=false
HOME_DIR="${HOME:-}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --json) JSON=true ;;
        --quiet) QUIET=true ;;
        --home) HOME_DIR="${2:-}"; shift ;;
        --show-prompt-value) SHOW_PROMPT_VALUE=true ;;
        -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print} NR>1 && !/^#/ {exit}' "$0"; exit 0 ;;
        *) echo "verify_launcher: unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ -z "$HOME_DIR" ]; then
    echo "verify_launcher: HOME is unset and no --home was given" >&2
    exit 2
fi
export HOME="$HOME_DIR"

BASHRC="$HOME/.bashrc"
RUN_DIR="$HOME/.bare-ai/run"
BEGIN_MARKER="# START: BARE-AI-AGENT WORKER BASHRC MODIFICATIONS:"
END_MARKER="# END: BARE-AI-AGENT WORKER BASHRC MODIFICATIONS:"
LOADER_MARKER="# BARE-AI Hybrid Loader"

FAILURES=""
fail() { FAILURES="$FAILURES $1"; }

# --- 1. Is the managed block present, and in which layout? --------------------
BLOCK_STATE="missing"
if [ -f "$BASHRC" ]; then
    if grep -qF "$BEGIN_MARKER" "$BASHRC" && grep -qF "$END_MARKER" "$BASHRC"; then
        BLOCK_STATE="marked"
    elif grep -qF "$LOADER_MARKER" "$BASHRC"; then
        BLOCK_STATE="legacy"
    fi
fi
[ "$BLOCK_STATE" = "missing" ] && fail "no BARE-AI block found in $BASHRC"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
BLOCK_FILE="$TMP_DIR/block.sh"

# Extract only the managed block: awk, not a sed range, so no shell escaping is
# needed and nothing outside the markers can be touched.
if [ "$BLOCK_STATE" = "marked" ]; then
    awk -v b="$BEGIN_MARKER" -v e="$END_MARKER" '$0==b{keep=1} keep{print} $0==e{keep=0}' "$BASHRC" > "$BLOCK_FILE"
elif [ "$BLOCK_STATE" = "legacy" ]; then
    awk -v m="$LOADER_MARKER" '$0==m{keep=1} keep{print}' "$BASHRC" > "$BLOCK_FILE"
else
    : > "$BLOCK_FILE"
fi

# --- 2. Does sourcing the block define bare() without a tty? ------------------
BARE_TYPE=""
if [ -s "$BLOCK_FILE" ]; then
    BARE_TYPE="$(bash -c 'source "$1" >/dev/null 2>&1; type -t bare' verify "$BLOCK_FILE" 2>/dev/null | head -1)"
fi
[ "$BARE_TYPE" = "function" ] || fail "bare() is not defined when the block is sourced non-interactively"

# --- 3. Does the block carry the prompt-file handoff, and not the legacy export?
if [ -s "$BLOCK_FILE" ]; then
    if grep -qF "BARE_AI_SYSTEM_PROMPT_FILE" "$BLOCK_FILE"; then
        PROMPT_HANDOFF="yes"
    else
        PROMPT_HANDOFF="no"
        fail "block has no BARE_AI_SYSTEM_PROMPT_FILE handoff (stale launcher)"
    fi
    if grep -qE '^[[:space:]]*export BARE_AI_SYSTEM_PROMPT=' "$BLOCK_FILE"; then
        LEGACY_EXPORT="present"
        fail "block still exports the oversized BARE_AI_SYSTEM_PROMPT string"
    else
        LEGACY_EXPORT="absent"
    fi
else
    PROMPT_HANDOFF="unknown"
    LEGACY_EXPORT="unknown"
fi

# --- 4. Evaluate the handoff logic itself with a throwaway prompt -------------
HANDOFF_FILE="$TMP_DIR/handoff.sh"
PROMPT_FILE=""

# The legacy in-environment prompt is reported by STATE and SIZE, not by value:
# an agent session can carry a ~40 KB BARE_AI_SYSTEM_PROMPT, and dumping that
# into a log is both huge and unnecessary. Use --show-prompt-value to opt in.
LEGACY_PROMPT_STATE="unset"
LEGACY_PROMPT_BYTES="0"
LEGACY_PROMPT_VALUE=""
if [ -n "${BARE_AI_SYSTEM_PROMPT:-}" ]; then
    LEGACY_PROMPT_STATE="set"
    LEGACY_PROMPT_BYTES="$(printf %s "$BARE_AI_SYSTEM_PROMPT" | wc -c | tr -d ' ')"
    if [ "$SHOW_PROMPT_VALUE" = true ]; then
        LEGACY_PROMPT_VALUE="$BARE_AI_SYSTEM_PROMPT"
    fi
fi

if [ "$PROMPT_HANDOFF" = "yes" ]; then
    {
        echo 'run_handoff() {'
        awk -v s='# --- FILE-BASED PROMPT HANDOFF' -v e='export BARE_AI_MODEL=' '$0 ~ s {inh=1} inh {print} $0 ~ e {inh=0}' "$BLOCK_FILE"
        echo '}'
        echo 'combined_const="verify-launcher probe prompt"'
        echo 'MODEL="verify-launcher-probe"'
        echo 'run_handoff'
        echo 'echo "PROMPT_FILE=${BARE_AI_SYSTEM_PROMPT_FILE:-}"'
    } > "$HANDOFF_FILE"

    HANDOFF_OUT="$(bash "$HANDOFF_FILE" 2>/dev/null)"
    PROMPT_FILE="$(echo "$HANDOFF_OUT" | grep -F 'PROMPT_FILE=' | head -1 | cut -d= -f2-)"
    if [ -z "$PROMPT_FILE" ]; then
        fail "the handoff logic produced no BARE_AI_SYSTEM_PROMPT_FILE"
    fi
fi

# --- 5. Is the handoff directory present and actually writable? ---------------
if [ -d "$RUN_DIR" ]; then
    RUN_DIR_STATE="present"
    RUN_DIR_MODE="$(stat -c %a "$RUN_DIR" 2>/dev/null || echo unknown)"
else
    RUN_DIR_STATE="missing"
    RUN_DIR_MODE="n/a"
    fail "$RUN_DIR is missing (the installer should create it)"
fi

WRITABLE="unknown"
if [ -n "$PROMPT_FILE" ]; then
    PROBE_DIR="$(dirname "$PROMPT_FILE")"
    PROBE_FILE="$PROBE_DIR/.verify-launcher-probe"
    if (umask 077; touch "$PROBE_FILE") 2>/dev/null; then
        WRITABLE="yes"
        rm -f "$PROBE_FILE"
    else
        WRITABLE="no"
        fail "cannot write a prompt file in $PROBE_DIR"
    fi
fi

BASHRC_MTIME="missing"
[ -f "$BASHRC" ] && BASHRC_MTIME="$(date -r "$BASHRC" +%Y-%m-%dT%H:%M:%S%z 2>/dev/null || echo unknown)"

STATUS="ok"
[ -n "$FAILURES" ] && STATUS="failed"
[ -z "$FAILURES" ] && FAILURES="none"

if [ "$JSON" = true ]; then
    echo '{'
    echo '  "home": "'"$HOME_DIR"'",'
    echo '  "bashrc": "'"$BASHRC"'",'
    echo '  "bashrc_mtime": "'"$BASHRC_MTIME"'",'
    echo '  "block": "'"$BLOCK_STATE"'",'
    echo '  "bare_function": "'"$BARE_TYPE"'",'
    echo '  "prompt_file_handoff": "'"$PROMPT_HANDOFF"'",'
    echo '  "legacy_prompt_export": "'"$LEGACY_EXPORT"'",'
    echo '  "run_dir": "'"$RUN_DIR_STATE"'",'
    echo '  "run_dir_mode": "'"$RUN_DIR_MODE"'",'
    echo '  "prompt_file": "'"$PROMPT_FILE"'",'
    echo '  "prompt_file_writable": "'"$WRITABLE"'",'
    echo '  "legacy_prompt": "'"$LEGACY_PROMPT_STATE"'",'
    echo '  "legacy_prompt_bytes": "'"$LEGACY_PROMPT_BYTES"'",'
    echo '  "legacy_prompt_value": "'"$LEGACY_PROMPT_VALUE"'",'
    echo '  "failures": "'"$FAILURES"'",'
    echo '  "status": "'"$STATUS"'"'
    echo '}'
elif [ "$QUIET" = true ]; then
    echo "status=$STATUS"
else
    echo "home=$HOME_DIR"
    echo "bashrc=$BASHRC"
    echo "bashrc_mtime=$BASHRC_MTIME"
    echo "block=$BLOCK_STATE"
    echo "bare_function=$BARE_TYPE"
    echo "prompt_file_handoff=$PROMPT_HANDOFF"
    echo "legacy_prompt_export=$LEGACY_EXPORT"
    echo "run_dir=$RUN_DIR_STATE"
    echo "run_dir_mode=$RUN_DIR_MODE"
    echo "prompt_file=$PROMPT_FILE"
    echo "prompt_file_writable=$WRITABLE"
    echo "legacy_prompt=$LEGACY_PROMPT_STATE"
    echo "legacy_prompt_bytes=$LEGACY_PROMPT_BYTES"
    echo "legacy_prompt_value=$LEGACY_PROMPT_VALUE"
    echo "failures=$FAILURES"
    echo "status=$STATUS"
fi

[ "$STATUS" = "ok" ] || exit 1
exit 0
