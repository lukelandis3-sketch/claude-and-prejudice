---
description: Set up thinking-book with a guided native picker
argument-hint: optional title, URL, or file path
allowed-tools: AskUserQuestion, Bash(python3:*)
---

Guide the user through thinking-book setup using Claude Code's native AskUserQuestion
picker. Keep the flow short, friendly, and entirely inside Claude Code.

The user's optional starting request is: $ARGUMENTS

First, resolve the installed plugin root and inspect the current state exactly once. Do not
re-run either dynamic command later:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py" version`

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py" status`

Then use AskUserQuestion to collect no more than four decisions:

1. What to read: keep the current book, Gutenberg, EPUB/plain-text file, web article,
   Kindle My Clippings, Readwise, or Libby. If a query, URL, or path was supplied above,
   infer this choice and do not ask it again. Otherwise use a follow-up picker whose Other
   response can carry the search query, URL, or path.
2. Page turning: timer, once per completed Claude turn, or manual.
3. Display: graphical HUD plus spinner, compact book line plus spinner, or spinner only.
4. If timer mode was chosen, pace: 5, 8, 12, or 20 seconds.

Summarize the choices before applying them. The version output above contains a `running
from` absolute path. Append `/scripts/thinking_book.py` to that exact path and run every
applying command as `python3 "<absolute script path>" <subcommand>`. Use only that CLI;
never edit Claude configuration files directly:

- Import with the appropriate `gutenberg`, `load`, `read`, `clippings`, `readwise`, or
  `libby` subcommand.
- Apply the chosen display in this exact order:
  - graphical HUD plus spinner: `on`, then `hud on`;
  - compact book line plus spinner: `on`, then `hud off`;
  - spinner only: `on`, then `hud off`, then `pane off`.
- Apply `mode` and, for timer mode, `dwell` after the display commands.
- Quote every user-supplied query, URL, and path as one shell argument.
- If an import or configuration command fails, show its exact useful error and stop. Do
  not claim setup succeeded and do not retry a network request without asking.

Finish with the current dashboard by running the same absolute CLI path with one empty
quoted argument. Repeat the working page-turn controls from that dashboard: mention `!tb n`
and `!tb b` only when it shows them, otherwise mention `/thinking-book:n` and the offered
`install-cli` shortcut. Keep the final response brief and do not add limitations the user
did not encounter.
