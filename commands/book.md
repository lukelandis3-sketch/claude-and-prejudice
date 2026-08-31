---
description: Read or manage books
argument-hint: <title|url|file> | status | pause | library | more...
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py" "$ARGUMENTS"`

Output the text above verbatim. Nothing else.
