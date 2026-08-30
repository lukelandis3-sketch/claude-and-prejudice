---
description: Manage the book you are reading in the margins of Claude Code
argument-hint: <title|url|file> | status | pause | queue | more...
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py" "$ARGUMENTS"`

Report the output above as-is, briefly. Do not re-run it.
