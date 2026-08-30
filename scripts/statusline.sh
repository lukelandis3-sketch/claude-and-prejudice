#!/bin/sh
# thinking-book status line: the reading surface that actually updates during a turn.
#
# Claude Code re-runs this once per new assistant message (plus on permission/vim-mode
# changes and on mount), with a 5s timeout. It is therefore the hot path: no Python, no
# JSON parsing, no full-file scans. All state it reads is written for it by Python.
#
# Any failure prints nothing and exits 0. A broken book must never break a status line.

set -u

# If a wrapped command turns out to be another thinking-book status line -- which happens
# when `pane on` runs from two different plugin roots -- this script would invoke itself
# forever, bounded only by Claude Code's 5s timeout. One exported marker ends that.
if [ "${TB_IN_STATUSLINE:-}" = "1" ]; then
    exit 0
fi
TB_IN_STATUSLINE=1
export TB_IN_STATUSLINE

TB_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/thinking-book"

# Claude Code pipes session JSON on stdin; a wrapped status line still expects it.
STDIN_JSON=$(cat 2>/dev/null || true)

emit_wrapped() {
    [ -f "$TB_DIR/wrapped.cmd" ] || return 0
    wrapped=$(cat "$TB_DIR/wrapped.cmd" 2>/dev/null) || return 0
    [ -n "$wrapped" ] || return 0
    printf '%s' "$STDIN_JSON" | sh -c "$wrapped" 2>/dev/null || true
}

# Without state there is nothing to read, but a wrapped status line must still render.
if [ ! -f "$TB_DIR/hot.env" ]; then
    emit_wrapped
    exit 0
fi

TB_MODE=timer
TB_DWELL=8
TB_PAUSED=0
TB_STATUSLINE=1
TB_PREFIX=''
# shellcheck disable=SC1090
. "$TB_DIR/hot.env" 2>/dev/null || true

# Presence means this surface has actually run in the current Claude Code session. The
# SessionStart hook removes it. A statusLine settings entry alone is not proof of life:
# newly installed commands may not mount until Claude Code restarts.
if [ "$TB_STATUSLINE" = "1" ] && [ ! -f "$TB_DIR/statusline.live" ]; then
    : > "$TB_DIR/statusline.live" 2>/dev/null || true
fi

read_int() {
    value=$(cat "$1" 2>/dev/null | tr -d ' \n\r') || value=''
    case "$value" in
        ''|*[!0-9]*) printf '%s' "$2" ;;
        *) printf '%s' "$value" ;;
    esac
}

POS=$(read_int "$TB_DIR/pos" 1)
LAST=$(read_int "$TB_DIR/last" 0)
COUNT=$(read_int "$TB_DIR/count" 0)

[ "$POS" -lt 1 ] && POS=1

# Timer mode: the page turns on the clock, one line per invocation at most, so walking
# away for an hour costs one line rather than four hundred.
if [ "$TB_STATUSLINE" = "1" ] && [ "$TB_PAUSED" = "0" ] && [ "$TB_MODE" = "timer" ] && [ "$COUNT" -gt 0 ]; then
    NOW=$(date +%s 2>/dev/null || echo 0)
    if [ "$LAST" -eq 0 ]; then
        # Cold start: no clock yet. Show this line and start the timer, rather than
        # treating the page as infinitely overdue and skipping the opening line.
        printf '%s\n' "$NOW" > "$TB_DIR/last.tmp.$$" 2>/dev/null &&
            mv "$TB_DIR/last.tmp.$$" "$TB_DIR/last" 2>/dev/null
    elif [ "$NOW" -gt 0 ] && [ $((NOW - LAST)) -ge "$TB_DWELL" ]; then
        if [ "$POS" -lt "$COUNT" ]; then
            POS=$((POS + 1))
            printf '%s\n' "$POS" > "$TB_DIR/pos.tmp.$$" 2>/dev/null &&
                mv "$TB_DIR/pos.tmp.$$" "$TB_DIR/pos" 2>/dev/null
        fi
        printf '%s\n' "$NOW" > "$TB_DIR/last.tmp.$$" 2>/dev/null &&
            mv "$TB_DIR/last.tmp.$$" "$TB_DIR/last" 2>/dev/null
    fi
fi

LINE=''
if [ "$TB_STATUSLINE" = "1" ] && [ -f "$TB_DIR/stream.txt" ]; then
    LINE=$(awk -v n="$POS" 'NR==n{print;exit}' "$TB_DIR/stream.txt" 2>/dev/null) || LINE=''
fi

WRAPPED_OUT=$(emit_wrapped)

if [ -n "$WRAPPED_OUT" ] && [ -n "$LINE" ]; then
    printf '%s\n%s%s\n' "$WRAPPED_OUT" "$TB_PREFIX" "$LINE"
elif [ -n "$WRAPPED_OUT" ]; then
    printf '%s\n' "$WRAPPED_OUT"
elif [ -n "$LINE" ]; then
    printf '%s%s\n' "$TB_PREFIX" "$LINE"
fi

exit 0
