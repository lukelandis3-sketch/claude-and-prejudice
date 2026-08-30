---
description: Manage the book you are reading in the margins of Claude Code
argument-hint: load <file> | gutenberg <title> | read <url> | libby <export.json> | feed add <url> | queue | status | mode timer|turn|manual | dwell <secs> | pane on|off | pause | resume | off
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py" $ARGUMENTS`

Report the output above to the user as-is. Keep it to a line or two; do not re-run the
command, and do not explain what thinking-book is unless they asked.
