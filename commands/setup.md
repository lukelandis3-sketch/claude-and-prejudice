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

Then use AskUserQuestion to collect no more than three decisions:

1. What to read: keep the current book or add something. If a query, URL, or path was
   supplied above, use it without asking again. Otherwise ask for one title, URL, or file
   path and let `add` detect its source; never ask the user to identify the file format.
2. Page turning: timer at the 250-words-per-minute default, once per completed Claude turn, or
   manual.
3. Display: graphical HUD plus spinner, compact book line plus spinner, or spinner only.

The version output contains a `running from` absolute path. Append
`/scripts/thinking_book.py` and run only that CLI; never edit Claude configuration files.
Summarize the choices, then apply at most these four commands:

1. `add "<title, URL, or path>"` unless keeping the current book.
2. `display hud|line|spinner`.
3. `mode timer|turn|manual`.
4. `pace 250` when timer mode was chosen, so upgraded fixed-second installations adopt
   the current default too.

Quote user input as one argument. On failure, show the exact useful error and stop; never
claim success or retry a network request without asking.

Finish with the current dashboard by running the same absolute CLI path with one empty
quoted argument. Repeat its page-turn controls exactly. Keep the final response brief and
do not add limitations the user did not encounter.
