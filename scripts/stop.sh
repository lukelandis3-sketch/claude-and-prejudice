#!/bin/sh
# Cheap Stop-hook dispatcher. Clean steady-state responses require shell builtins only;
# every ambiguous, stale, or malformed state falls back to the Python policy engine.
# All output is suppressed and every path exits 0 so reading can never block Claude.

CLI=${1:-}

run_python() {
    if [ -n "$CLI" ] && [ -f "$CLI" ]; then
        python3 "$CLI" advance --quiet </dev/null >/dev/null 2>&1 || :
    fi
    exit 0
}

if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    TB_DIR=$CLAUDE_CONFIG_DIR/thinking-book
elif [ -n "${HOME:-}" ]; then
    TB_DIR=$HOME/.claude/thinking-book
else
    exit 0
fi

TB_MODE=''
TB_PAUSED=''
TB_STATUSLINE=''
TB_SPINNER=''
TB_EXTRA=''
IFS=' ' read -r TB_MODE TB_PAUSED TB_STATUSLINE TB_SPINNER TB_EXTRA \
    2>/dev/null < "$TB_DIR/stop.control" || run_python
case "$TB_MODE" in timer|turn|manual) : ;; *) run_python ;; esac
case "$TB_PAUSED$TB_STATUSLINE$TB_SPINNER" in
    000|001|010|011|100|101|110|111) : ;;
    *) run_python ;;
esac
[ -z "$TB_EXTRA" ] || run_python

# A spinner can be skipped only when Python certified the exact immutable generation
# and numeric position it rendered. A status-line page turn makes this cursor dirty.
if [ "$TB_SPINNER" = "1" ]; then
    CUR_GEN=''
    CUR_POS=''
    CUR_EXTRA=''
    IFS=' ' read -r CUR_GEN CUR_POS CUR_EXTRA \
        2>/dev/null < "$TB_DIR/spinner.cursor" || run_python
    [ -z "$CUR_EXTRA" ] || run_python
    case "$CUR_GEN" in
        none) : ;;
        ''|*[!0-9a-f-]*) run_python ;;
    esac
    [ "${#CUR_GEN}" -le 64 ] || run_python
    case "$CUR_POS" in ''|0*|*[!0-9]*) run_python ;; esac
    [ "${#CUR_POS}" -le 12 ] || run_python

    GEN=''
    IFS= read -r GEN 2>/dev/null < "$TB_DIR/stream.gen" || GEN=''
    [ -n "$GEN" ] || GEN=none
    POS=''
    IFS= read -r POS 2>/dev/null < "$TB_DIR/pos" || POS=''
    [ "$CUR_GEN" = "$GEN" ] && [ "$CUR_POS" = "$POS" ] || run_python
fi

[ "$TB_PAUSED" = "1" ] && exit 0
[ "$TB_MODE" = "manual" ] && exit 0
[ "$TB_MODE" = "turn" ] && run_python

# A live status line owns the timer clock. If it is absent, Python remains the fallback
# clock so a newly installed pane still turns pages before Claude is restarted.
[ "$TB_STATUSLINE" = "1" ] || run_python
TB_SESSION_ID=${CLAUDE_CODE_SESSION_ID:-global}
case "$TB_SESSION_ID" in ''|*[!A-Za-z0-9_-]*) TB_SESSION_ID=global ;; esac
[ "${#TB_SESSION_ID}" -le 64 ] || TB_SESSION_ID=global
[ -f "$TB_DIR/statusline.live.$TB_SESSION_ID" ] || run_python

exit 0
