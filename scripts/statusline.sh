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
TB_SESSION_ID=${CLAUDE_CODE_SESSION_ID:-global}
case "$TB_SESSION_ID" in
    ''|*[!A-Za-z0-9_-]*) TB_SESSION_ID=global ;;
esac
TB_LIVE_MARKER="$TB_DIR/statusline.live.$TB_SESSION_ID"

# Claude Code pipes session JSON on stdin. Retain it only when a wrapped status line
# needs the bytes; otherwise drain it without a command-substitution copy in memory.
HAS_WRAPPED=0
STDIN_JSON=''
if [ -s "$TB_DIR/wrapped.cmd" ]; then
    HAS_WRAPPED=1
    STDIN_JSON=$(cat 2>/dev/null || true)
else
    cat >/dev/null 2>&1 || true
fi

emit_wrapped() {
    [ "$HAS_WRAPPED" = "1" ] || return 0
    wrapped=$(cat "$TB_DIR/wrapped.cmd" 2>/dev/null) || return 0
    [ -n "$wrapped" ] || return 0
    { printf '%s' "$STDIN_JSON" | sh -c "$wrapped"; } 2>/dev/null || true
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
TB_HUD=0
# shellcheck disable=SC1090
. "$TB_DIR/hot.env" 2>/dev/null || true

# Presence means this surface has actually run in the current Claude Code session. The
# SessionStart hook removes it. A statusLine settings entry alone is not proof of life:
# newly installed commands may not mount until Claude Code restarts.
if [ "$TB_STATUSLINE" = "1" ] && [ ! -f "$TB_LIVE_MARKER" ]; then
    : > "$TB_LIVE_MARKER" 2>/dev/null || true
fi

read_int() {
    destination=$1
    value=''
    if IFS= read -r value 2>/dev/null < "$2"; then
        :
    elif [ -z "$value" ]; then
        value=''
    fi
    case "$value" in
        ''|*[!0-9]*) value=$3 ;;
    esac
    # `value` is digits or a trusted numeric default, so this assignment cannot inject
    # shell syntax. Avoiding command substitution here removes one fork per state value.
    eval "$destination=\$value"
}

read_int POS "$TB_DIR/pos" 1
read_int LAST "$TB_DIR/last" 0
GEN=''
IFS= read -r GEN 2>/dev/null < "$TB_DIR/stream.gen" || GEN=''
case "$GEN" in
    ''|*[!0-9a-f-]*) GEN='' ;;
esac
if [ -n "$GEN" ]; then
    STREAM_DIR="$TB_DIR/stream-generations/$GEN"
    read_int COUNT "$STREAM_DIR/count" 0
else
    STREAM_DIR=''
    COUNT=0
fi

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
HUD=''
if [ "$TB_STATUSLINE" = "1" ] && [ -n "$STREAM_DIR" ] && [ "$POS" -le "$COUNT" ]; then
    SHARD=$(((POS - 1) / 256))
    ROW=$(((POS - 1) % 256 + 1))
    LINE=$(sed -n "${ROW}p" "$STREAM_DIR/$SHARD.txt" 2>/dev/null) || LINE=''
    if [ "$TB_HUD" = "1" ]; then
        HUD=$(sed -n "${ROW}p" "$STREAM_DIR/$SHARD.hud" 2>/dev/null) || HUD=''
        if [ -n "$HUD" ]; then
            if [ "$TB_MODE" = "timer" ]; then
                HUD="$HUD · timer ${TB_DWELL}s"
            else
                HUD="$HUD · $TB_MODE"
            fi
            [ "$TB_PAUSED" = "1" ] && HUD="$HUD · paused"
        fi
    fi
fi

WRAPPED_OUT=''
if [ "$HAS_WRAPPED" = "1" ]; then
    WRAPPED_OUT=$(emit_wrapped)
fi

if [ -n "$WRAPPED_OUT" ]; then
    printf '%s\n' "$WRAPPED_OUT"
fi
if [ -n "$HUD" ]; then
    printf '%s\n' "$HUD"
fi
if [ -n "$LINE" ]; then
    printf '%s%s\n' "$TB_PREFIX" "$LINE"
fi

exit 0
