---
description: Manage the book you are reading in the margins of Claude Code
argument-hint: <title|url|file> | status | pause | queue | more...
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py" "$ARGUMENTS"`

Output the text above verbatim. Nothing else.
