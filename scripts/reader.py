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

POLL_SECONDS = 1.0
FOOTER_HINT = "space next · b back · r redraw · q quit"


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
    return None


def frame(line, title, position, total, width=80, mode=None):
    """Render the pane as a list of lines. Pure -- no terminal, no state."""
    # Honour the width given: a caller passing the real terminal width must never get a
    # row wider than it, however cramped the window.
    width = max(1, int(width))
    body = textwrap.wrap(line, width=width) if line else ["(nothing queued)"]
    body = [row[:width] for row in body] or [""]

    percent = (position / total * 100) if total else 0.0
    left = "%s — %d/%d (%.1f%%)" % (title or "untitled", position, total, percent)
    if mode:
        left += " · %s" % mode

    footer = "%s   %s" % (left, FOOTER_HINT)
    if len(footer) > width:
        footer = left[:width]
    return body + ["", footer]


def read_key(timeout=POLL_SECONDS):
    """One keypress, or None when the timeout expires so the caller can re-check state.

    Reads the raw descriptor rather than `sys.stdin.read(1)`: the text layer buffers, so a
    one-byte read drained the whole arrow-key escape sequence off the fd and left the
    caller holding a bare ESC -- which reads as "quit".
    """
    if not select.select([sys.stdin], [], [], timeout)[0]:
        return None
    try:
        data = os.read(sys.stdin.fileno(), 8)
    except OSError:
        return None
    if not data:
        return None
    return data.decode("utf-8", errors="replace")


def run(state, on_advance, on_back):
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
    try:
        tty.setcbreak(fd)
        while True:
            snapshot = state()
            if snapshot != last_drawn:
                width = shutil.get_terminal_size((80, 24)).columns
                line, title, position, total, mode = snapshot
                # Clear and home, then draw. Cheap enough at human keypress rates.
                sys.stdout.write("\x1b[2J\x1b[H")
                sys.stdout.write("\n".join(frame(line, title, position, total, width, mode)))
                sys.stdout.write("\n")
                sys.stdout.flush()
                last_drawn = snapshot

            action = action_for(read_key())
            if action == "quit":
                return 0
            if action == "advance":
                on_advance()
            elif action == "back":
                on_back()
            elif action == "redraw":
                last_drawn = None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
