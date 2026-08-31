"""The companion reading pane: one keypress turns the page, outside the Claude thread.

Claude Code's own keybindings map only to a closed enum of built-in actions, and a plugin
slash command costs a model turn. Running here, in a split, sidesteps both: space advances
and nothing touches the conversation.

The terminal handling is confined to `run`; everything above it is pure so it can be tested
without a tty.
"""

import os
import select
import shutil
import sys
import textwrap

ADVANCE_KEYS = {" ", "n", "j", "\x1b[C", "\x1b[B"}   # space, n, j, right, down
BACK_KEYS = {"b", "k", "\x1b[D", "\x1b[A"}           # b, k, left, up
REDRAW_KEYS = {"r", "\x0c"}                          # r, ctrl-L
QUIT_KEYS = {"q", "\x03", "\x04", "\x1b"}            # q, ctrl-C, ctrl-D, esc
PAUSE_KEYS = {"p"}
FASTER_KEYS = {"+", "="}
SLOWER_KEYS = {"-"}

POLL_SECONDS = 1.0
FOOTER_HINT = "space/→ next · ← back · p pause · +/- pace · q quit"
ENTER_SCREEN = "\x1b[?1049h\x1b[?25l"
EXIT_SCREEN = "\x1b[?25h\x1b[?1049l"
_PENDING = bytearray()


def action_for(key):
    """Map a keypress to an action name. Unknown keys do nothing at all."""
    if key in QUIT_KEYS:
        return "quit"
    if key in ADVANCE_KEYS:
        return "advance"
    if key in BACK_KEYS:
        return "back"
    if key in REDRAW_KEYS:
        return "redraw"
    if key in PAUSE_KEYS:
        return "pause"
    if key in FASTER_KEYS:
        return "faster"
    if key in SLOWER_KEYS:
        return "slower"
    return None


def frame(line, title, position, total, width=80, mode=None, context=None, height=24):
    """Render the pane as a list of lines. Pure -- no terminal, no state."""
    # Honour the width given: a caller passing the real terminal width must never get a
    # row wider than it, however cramped the window.
    width = max(1, int(width))
    height = max(1, int(height))
    passages = context if context else [(line or "(nothing queued)", True)]
    body, current_rows = [], []
    for passage, current in passages:
        prefix = "📖 " if current else "  "
        content_width = max(1, width - len(prefix))
        wrapped = textwrap.wrap(passage, width=content_width) or [""]
        if current:
            current_rows.append(len(body))
        body.append((prefix + wrapped[0])[:width])
        body.extend((" " * len(prefix) + row)[:width] for row in wrapped[1:])

    percent = (position / total * 100) if total else 0.0
    left = "%s — %d/%d (%.1f%%)" % (title or "untitled", position, total, percent)
    if mode:
        left += " · %s" % mode
    if height >= 6:
        trailer = ["", FOOTER_HINT[:width], left[:width]]
    elif height >= 4:
        trailer = [left[:width]]
    else:
        trailer = []

    available = max(1, height - len(trailer))
    anchor = current_rows[0] if current_rows else 0
    start = max(0, anchor - available // 2)
    start = min(start, max(0, len(body) - available))
    body = body[start:start + available]

    return (body + trailer)[:height]


def clear_pending_keys():
    del _PENDING[:]


def _take_pending():
    if not _PENDING:
        return None
    if (len(_PENDING) >= 3 and _PENDING[0] == 0x1b and _PENDING[1] == ord("[")
            and _PENDING[2] in b"ABCD"):
        raw = bytes(_PENDING[:3])
        del _PENDING[:3]
        return raw.decode("ascii")
    raw = bytes([_PENDING[0]])
    del _PENDING[0]
    return raw.decode("utf-8", errors="replace")


def read_key(timeout=POLL_SECONDS):
    """One keypress, or None when the timeout expires so the caller can re-check state.

    Reads the raw descriptor rather than `sys.stdin.read(1)`: the text layer buffers, so a
    one-byte read drained the whole arrow-key escape sequence off the fd and left the
    caller holding a bare ESC -- which reads as "quit".
    """
    pending = _take_pending()
    if pending is not None:
        return pending
    if not select.select([sys.stdin], [], [], timeout)[0]:
        return None
    try:
        data = os.read(sys.stdin.fileno(), 32)
    except OSError:
        return None
    if not data:
        return None
    _PENDING.extend(data)
    return _take_pending()


def run(state, on_advance, on_back, on_pause=None, on_faster=None, on_slower=None):
    """Drive the pane until the reader quits.

    `state` returns (line, title, position, total, mode); the callbacks move the bookmark.
    """
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover - Windows
        print(state()[0] or "(nothing queued)")
        print("The reader pane needs a Unix terminal.", file=sys.stderr)
        return 1

    if not sys.stdin.isatty():
        print(state()[0] or "(nothing queued)")
        print("Not a terminal -- run `book reader` in a real shell for the pane.",
              file=sys.stderr)
        return 1

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    last_drawn = None
    last_viewport = None
    try:
        tty.setcbreak(fd)
        clear_pending_keys()
        sys.stdout.write(ENTER_SCREEN)
        while True:
            snapshot = state()
            size = shutil.get_terminal_size((80, 24))
            viewport = (size.columns, size.lines)
            if snapshot != last_drawn or viewport != last_viewport:
                context, title, position, total, mode = snapshot
                line = next((text for text, current in context if current), "")
                # Clear and home, then draw. Cheap enough at human keypress rates.
                sys.stdout.write("\x1b[2J\x1b[H")
                sys.stdout.write("\n".join(frame(
                    line, title, position, total, size.columns, mode,
                    context=context, height=size.lines)))
                sys.stdout.write("\n")
                sys.stdout.flush()
                last_drawn = snapshot
                last_viewport = viewport

            action = action_for(read_key())
            if action == "quit":
                return 0
            if action == "advance":
                on_advance()
            elif action == "back":
                on_back()
            elif action == "redraw":
                last_drawn = None
            elif action == "pause" and on_pause:
                on_pause()
            elif action == "faster" and on_faster:
                on_faster()
            elif action == "slower" and on_slower:
                on_slower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write(EXIT_SCREEN)
        sys.stdout.flush()
