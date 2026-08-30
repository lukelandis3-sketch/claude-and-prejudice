---
description: Reset reading to recommended defaults
argument-hint: title, URL, or file path
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/thinking_book.py" start "$ARGUMENTS"`

Output the text above verbatim. Nothing else.
