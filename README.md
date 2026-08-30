# thinking-book

Read a book in the margins of Claude Code.

This plugin replaces the little words Claude Code shows while it works — *Pondering…*,
*Percolating…*, *Reticulating…* — with successive lines of whatever you're reading, and
puts the same line in your status line, where it can turn pages during a turn.

```
 ✻ Call me Ishmael.… (12s · esc to interrupt)
```

## Install

```
/plugin marketplace add lukelandis3-sketch/claude-thinking-book
/plugin install thinking-book
```

Then pick something to read and turn on the status line surface:

```
/book gutenberg moby dick
/book pane on
```

Restart Claude Code once so the status line takes effect.

## Reading

Plugin commands are namespaced, so they are `/thinking-book:n`, `/thinking-book:book`, and
so on. That is a lot of keystrokes for a page turn — see *A real `/n`* below.

| Command | What it does |
|---|---|
| `/thinking-book:n` | Turn the page — advance one line |
| `/thinking-book:b` | Back one line |
| `/book status` | Title, author, position, percent read, current line |
| `/book queue` | What's queued, and what you're in the middle of |
| `/book mode timer\|turn\|manual` | How pages turn (below) |
| `/book dwell <seconds>` | Reading pace for timer mode (default 8) |
| `/book pause` / `/book resume` | Freeze on a line, or carry on |
| `/book pane on\|off` | Attach or detach the status line surface |
| `/book off` | Full stop — stock spinner verbs and your own status line back |
| `/book repair` | Undo a self-wrapped status line (see below) |
| `/book refresh <secs\|off>` | Set `statusLine.refreshInterval` where your version supports it |

### A real `/n`

Namespacing is imposed by the plugin system and cannot be turned off from inside a plugin.
To get a genuine two-keystroke page turn, add a *personal* command — files in
`~/.claude/commands/` are not namespaced:

```sh
mkdir -p ~/.claude/commands
cat > ~/.claude/commands/n.md <<'EOF'
---
description: Turn the page
allowed-tools: Bash(python3:*)
---
!`python3 ~/.claude/plugins/*/thinking-book/scripts/thinking_book.py next`
EOF
```

Adjust the path to wherever the plugin is installed.

### Advance modes

- **`timer`** (default) — pages turn on the clock, at most one line per refresh. Walking
  away for an hour costs you one line, not four hundred.
- **`turn`** — exactly one line per assistant turn, regardless of how long it took.
- **`manual`** — the line holds until you type `/n`.

## What you can read

| Command | Source | Gives you |
|---|---|---|
| `/book load <file.epub>` | A DRM-free EPUB you own | Full sequential prose |
| `/book load <file.txt>` | Any plain text file | Full sequential prose |
| `/book gutenberg <title\|id>` | [Project Gutenberg](https://gutendex.com) | Full sequential prose |
| `/book read <url>` | A web article | Full sequential prose |
| `/book feed add <url>` | An RSS or Atom feed | New articles, queued automatically |
| `/book libby <export.json>` | A Libby *Reading Journey* export | Your highlights |

Items form a queue and are read in order; when one runs out the next begins. Feeds top the
queue up at session start, in the background, at most three new articles per feed per hour.

### About Kindle and Libby

There is no legitimate way to get the full text of a current commercial read into this
plugin, and it does not try.

- **Amazon has no public API** for your Kindle library, and Kindle books are DRM'd.
  (Readwise doesn't use an API either — it scrapes `read.amazon.com/notebook` with a
  browser extension.)
- **Libby/OverDrive has no public API**, but it does let you
  [export a title's Reading Journey](https://help.libbyapp.com/en-us/6151.htm) — highlights,
  notes, chapter and position — as JSON, and that export keeps working after you return
  the book. `/book libby` reads that file.

**This plugin does not strip DRM** — not Kindle KFX, not Adobe DRM, not Apple Books. An
encrypted EPUB is rejected with an explanation rather than worked around. For sequential
prose, use DRM-free copies (Standard Ebooks, No Starch, O'Reilly, most Humble bundles),
the public domain, or the open web.

Readwise and Kindle's `My Clippings.txt` are not supported yet. Every importer implements
the same `load(arg) -> (meta, fragments)` interface, so both would slot in without
touching anything else.

## How it works

Claude Code has a documented `spinnerVerbs` setting:

```json
{ "spinnerVerbs": { "mode": "replace", "verbs": ["Call me Ishmael."] } }
```

It picks from that list **at random, once, when the spinner mounts**. So the plugin writes
a *single-element* list — random-of-one is deterministic — and rewrites it as you read.
Settings files are watched, so the change lands without a restart.

The status line is the livelier surface. Claude Code re-runs a `statusLine` command **once
per assistant message**, so it updates several times inside a single turn. `statusline.sh`
is the hot path: no Python, no JSON parsing, no full-file scans — Python pre-chunks
everything at import into one flat stream file, and the shell does a single indexed lookup.
It measures ~10 ms even at line 25,000 of a 25,000-line novel, against a 5 s timeout.

State lives in `~/.claude/thinking-book/`. JSON files are the human-readable record;
flat one-value files (`pos`, `last`, `count`, `hot.env`) exist so the hot path can read
them with a single `cat`.

## Honest limitations

- **Completed turns always show stock verbs.** This is the big one. The lines that stay in
  your scrollback — `Baked for 1s · done`, `Brewed for 1s · done` — come from a *separate,
  hardcoded* past-tense list with no settings override:
  ```js
  cW1 = ["Baked","Brewed","Churned","Cogitated","Cooked","Crunched","Sautéed","Worked"]
  ```
  `spinnerVerbs` replaces only the *present-tense* verb on the **live** spinner, which
  exists only while a turn is running. So the book appears while Claude is thinking and
  leaves no trace once the turn ends. Nothing a plugin can do changes this — and it is the
  strongest argument for the status line being the primary surface, since that one persists.
- **The spinner text cannot change during a turn.** It's captured in a `useState`
  initializer when the spinner mounts. A manual advance lands on the *next* spinner.
- **No single key can turn the page.** Claude Code's keybindings map to a closed enum of
  built-in actions; there's no "run a command" action to bind. `/n` is the shortest path.
- **The status line has no wall-clock refresh** — it advances per assistant message, so a
  long single tool call holds its line.
- **Subagent panes show the stock verbs.** Task spinners bypass `spinnerVerbs` entirely.
- **An in-progress todo outranks the spinner verb**, so reading pauses during todo work.
- **`disableAllHooks: true` disables the status line too**, and the plugin goes dark.
- **Concurrent sessions** share one global setting and will interleave each other's lines.
  Locking keeps the bookmark consistent, so nothing is lost.
- **`refreshInterval` may not exist in your version.** Claude Code 2.1.251 has no such key,
  so `/book refresh` is a no-op there; other versions (and tools like ccstatusline) do use
  it. Harmless either way — unknown settings keys are ignored.
- **Your `settings.json` gets reformatted** on first write. A copy of the original is kept
  at `~/.claude/thinking-book/settings.backup.json`, and `/book off` restores every key
  the plugin touched.

`spinnerVerbs` and `statusLine` are documented settings. Single-element sampling is
inference from the current implementation — if a future release re-picks verbs on a timer,
this degrades to showing nearby lines rather than breaking.

## If the status line repeats the same line over and over

Versions before 0.2 could wrap their *own* status line when `pane on` ran from two
different paths — a git clone once, the installed plugin the next time. The script then
re-read the same global `wrapped.cmd` and invoked itself, recursing until Claude Code's 5 s
timeout killed it and printing the same line a dozen times over.

```
/book repair
```

0.2 makes this impossible three ways over: an exported `TB_IN_STATUSLINE` guard that stops
recursion at the first level, path-independent identity so `pane on` recognises its own
script wherever it was installed from, and `repair` to unwind machines already in that state.

Unlike most status line tools — [ccstatusline](https://github.com/sirmalloc/ccstatusline) and
friends simply overwrite `statusLine` — thinking-book wraps whatever you already had and runs
it alongside the book. `/book off` puts it back exactly as it was.

## Development

No dependencies beyond the Python 3 standard library.

```
python3 -m unittest discover -s tests -p "test_*.py"
```

## Licence

MIT
