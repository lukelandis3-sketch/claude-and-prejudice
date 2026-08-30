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
[ "${#TB_SESSION_ID}" -gt 64 ] && TB_SESSION_ID=global
TB_LIVE_MARKER="$TB_DIR/statusline.live.$TB_SESSION_ID"

# Claude Code pipes session JSON on stdin. Retain it only when a wrapped status line
# needs the bytes; otherwise drain it without a command-substitution copy in memory.
HAS_WRAPPED=0
STDIN_JSON=''
if [ -s "$TB_DIR/wrapped.cmd" ]; then
    HAS_WRAPPED=1
    STDIN_JSON=$(cat 2>/dev/null || true)
else
    while IFS= read -r _tb_stdin; do :; done
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
TB_WPM=0
TB_PAUSED=0
TB_STATUSLINE=1
TB_PREFIX=''
TB_HUD=0
# shellcheck disable=SC1090
. "$TB_DIR/hot.env" 2>/dev/null || true

WPM_OK=0
case "$TB_WPM" in
    ''|0*|*[!0-9]*) TB_WPM=0 ;;
    *)
        if [ "${#TB_WPM}" -le 4 ] && [ "$TB_WPM" -le 1000 ]; then
            WPM_OK=1
        else
            TB_WPM=0
        fi
        ;;
esac
case "$TB_DWELL" in
    ''|0*|*[!0-9]*) TB_DWELL=8 ;;
    *)
        if [ "${#TB_DWELL}" -gt 5 ] || [ "$TB_DWELL" -gt 86400 ]; then
            TB_DWELL=8
        fi
        ;;
esac

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
    [ "${#value}" -gt 12 ] && value=$3
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
[ "${#GEN}" -gt 64 ] && GEN=''
if [ -n "$GEN" ]; then
    STREAM_DIR="$TB_DIR/stream-generations/$GEN"
    read_int COUNT "$STREAM_DIR/count" 0
else
    STREAM_DIR=''
    COUNT=0
fi

[ "$POS" -lt 1 ] && POS=1

LINE=''
WORDS=1
SHARD=0
ROW=0
load_line() {
    LINE=''
    WORDS=1
    if [ "$TB_STATUSLINE" = "1" ] && [ -n "$STREAM_DIR" ] && [ "$POS" -le "$COUNT" ]; then
        SHARD=$(((POS - 1) / 256))
        ROW=$(((POS - 1) % 256 + 1))
        RECORD=$(sed -n "${ROW}{p;q;}" "$STREAM_DIR/$SHARD.txt" 2>/dev/null) || RECORD=''
        TAB='	'
        case "$RECORD" in
            [0-9]*"$TAB"*)
                WORDS=${RECORD%%"$TAB"*}
                LINE=${RECORD#*"$TAB"}
                case "$WORDS" in ''|0*|*[!0-9]*) WORDS=1 ;; esac
                [ "${#WORDS}" -gt 4 ] && WORDS=1000
                [ "$WORDS" -gt 1000 ] 2>/dev/null && WORDS=1000
                ;;
            *) LINE=$RECORD ;;
        esac
    fi
    return 0
}
[ "$WPM_OK" = "1" ] && load_line

# Timer mode: the page turns on the clock, one line per invocation at most, so walking
# away for an hour costs one line rather than four hundred.
if [ "$TB_STATUSLINE" = "1" ] && [ "$TB_PAUSED" = "0" ] && [ "$TB_MODE" = "timer" ] && [ "$COUNT" -gt 0 ]; then
    INTERVAL=$TB_DWELL
    if [ "$WPM_OK" = "1" ]; then
        INTERVAL=$(((WORDS * 60 + TB_WPM - 1) / TB_WPM))
        [ "$INTERVAL" -lt 2 ] && INTERVAL=2
        [ "$INTERVAL" -gt 30 ] && INTERVAL=30
    fi
    NOW=$(date +%s 2>/dev/null || echo 0)
    if [ "$LAST" -eq 0 ]; then
        # Cold start: no clock yet. Show this line and start the timer, rather than
        # treating the page as infinitely overdue and skipping the opening line.
        printf '%s\n' "$NOW" > "$TB_DIR/last.tmp.$$" 2>/dev/null &&
            mv "$TB_DIR/last.tmp.$$" "$TB_DIR/last" 2>/dev/null
    elif [ "$NOW" -gt 0 ] && [ $((NOW - LAST)) -ge "$INTERVAL" ]; then
        if [ "$POS" -lt "$COUNT" ]; then
            # A Python rebuild can atomically switch generations while this shell is
            # running. Never write an old numeric cursor into the new book layout.
            GEN_NOW=''
            IFS= read -r GEN_NOW 2>/dev/null < "$TB_DIR/stream.gen" || GEN_NOW=''
            if [ "$GEN_NOW" = "$GEN" ]; then
                POS=$((POS + 1))
                printf '%s\n' "$POS" > "$TB_DIR/pos.tmp.$$" 2>/dev/null &&
                    mv "$TB_DIR/pos.tmp.$$" "$TB_DIR/pos" 2>/dev/null
                load_line
            fi
        fi
        printf '%s\n' "$NOW" > "$TB_DIR/last.tmp.$$" 2>/dev/null &&
            mv "$TB_DIR/last.tmp.$$" "$TB_DIR/last" 2>/dev/null
    fi
fi

[ "$WPM_OK" = "0" ] && load_line

HUD=''
if [ "$TB_STATUSLINE" = "1" ] && [ -n "$STREAM_DIR" ] && [ "$POS" -le "$COUNT" ]; then
    if [ "$TB_HUD" = "1" ]; then
        HUD=$(sed -n "${ROW}{p;q;}" "$STREAM_DIR/$SHARD.hud" 2>/dev/null) || HUD=''
        if [ -n "$HUD" ]; then
            # Normalise already-published HUD rows in place; users should not have to
            # re-import or rebuild a book when the visual marker moves to the prose.
            case "$HUD" in
                "READ HERE · "*) HUD=${HUD#"READ HERE · "} ;;
                "📖 "*) HUD=${HUD#"📖 "} ;;
            esac
            if [ "$TB_MODE" = "timer" ]; then
                if [ "$WPM_OK" = "1" ]; then
                    HUD="$HUD · ${TB_WPM} wpm"
                else
                    HUD="$HUD · timer ${TB_DWELL}s"
                fi
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
    BOOK_LINE="📖 ${TB_PREFIX}${LINE}"
    # Claude reserves a few columns around the status line. Keep a readable 100-column
    # ceiling, honour narrower exported terminal widths, and preserve every word by
    # wrapping only the uncommon over-long fragment. Short lines spawn no extra process.
    DISPLAY_WIDTH=${COLUMNS:-108}
    case "$DISPLAY_WIDTH" in
        ''|*[!0-9]*) DISPLAY_WIDTH=108 ;;
    esac
    [ "${#DISPLAY_WIDTH}" -gt 4 ] && DISPLAY_WIDTH=108
    if [ "$DISPLAY_WIDTH" -gt 108 ]; then
        DISPLAY_WIDTH=100
    elif [ "$DISPLAY_WIDTH" -gt 8 ]; then
        DISPLAY_WIDTH=$((DISPLAY_WIDTH - 8))
    fi
    [ "$DISPLAY_WIDTH" -lt 1 ] && DISPLAY_WIDTH=1
    if [ "${#BOOK_LINE}" -le "$DISPLAY_WIDTH" ]; then
        printf '%s\n' "$BOOK_LINE"
    elif command -v fold >/dev/null 2>&1; then
        printf '%s\n' "$BOOK_LINE" | fold -s -w "$DISPLAY_WIDTH" 2>/dev/null ||
            printf '%s\n' "$BOOK_LINE"
    else
        printf '%s\n' "$BOOK_LINE"
    fi
fi

exit 0
