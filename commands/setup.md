---
description: Set up thinking-book with a guided native picker
argument-hint: optional title, URL, or file path
allowed-tools: AskUserQuestion Bash(python3:*)
---

Guide the user through thinking-book setup using Claude Code's native AskUserQuestion
picker. Keep the flow short, friendly, and entirely inside Claude Code.

The user's optional starting request is: $ARGUMENTS

First, inspect the current state exactly once:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py" status`

Then use AskUserQuestion to collect no more than four decisions:

1. What to read: keep the current book, Gutenberg, EPUB/plain-text file, web article,
   Kindle My Clippings, Readwise, or Libby. If a query, URL, or path was supplied above,
   infer this choice and do not ask it again. Otherwise use a follow-up picker whose Other
   response can carry the search query, URL, or path.
2. Page turning: timer, once per completed Claude turn, or manual.
3. Display: graphical HUD plus spinner, compact book line plus spinner, or spinner only.
4. If timer mode was chosen, pace: 5, 8, 12, or 20 seconds.

Summarize the choices before applying them. Use only the plugin CLI below; never edit
Claude configuration files directly:

- Import with the appropriate `gutenberg`, `load`, `read`, `clippings`, `readwise`, or
  `libby` subcommand.
- Apply `mode`, `dwell`, `on`, `pane on|off`, and `hud on|off` as required.
- Quote every user-supplied query, URL, and path as one shell argument.
- If an import or configuration command fails, show its exact useful error and stop. Do
  not claim setup succeeded and do not retry a network request without asking.

Finish with the current dashboard by running the CLI with one empty quoted argument.
Mention that `!tb n` and `!tb b` turn pages without a model turn. Keep the final response
brief and do not add limitations the user did not encounter.
