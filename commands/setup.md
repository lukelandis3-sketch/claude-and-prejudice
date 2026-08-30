---
description: Start your own book with sensible defaults
argument-hint: title, URL, or file path
allowed-tools: Bash(python3:*)
---

The book is: $ARGUMENTS

If it is blank, run no tools and reply only:
`Choose a book: /thinking-book:setup <title, URL, or file path>`

Otherwise run exactly one command: Python at
`${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py`, subcommand `start`, then the book as one
quoted argument.

Return output verbatim. No preflight, preset, retry, summary, or dashboard.
