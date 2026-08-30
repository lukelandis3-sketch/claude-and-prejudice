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

The version output contains a `running from` absolute path. Append
`/scripts/thinking_book.py` and run only that CLI; never edit Claude configuration files.
Summarize the choices, then apply at most these three commands:

1. `add "<title, URL, or path>"` unless keeping the current book.
2. `display hud|line|spinner`.
3. `mode timer|turn|manual`, followed by `dwell <seconds>` only for timer mode.

Quote user input as one argument. On failure, show the exact useful error and stop; never
claim success or retry a network request without asking.

Finish with the current dashboard by running the same absolute CLI path with one empty
quoted argument. Repeat its page-turn controls exactly. Keep the final response brief and
do not add limitations the user did not encounter.
