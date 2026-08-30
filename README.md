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

| Command | What it does |
|---|---|
| `/n` | Turn the page — advance one line |
| `/b` | Back one line |
| `/book status` | Title, author, position, percent read, current line |
| `/book queue` | What's queued, and what you're in the middle of |
| `/book mode timer\|turn\|manual` | How pages turn (below) |
| `/book dwell <seconds>` | Reading pace for timer mode (default 8) |
| `/book pause` / `/book resume` | Freeze on a line, or carry on |
| `/book pane on\|off` | Attach or detach the status line surface |
| `/book off` | Full stop — stock spinner verbs and your own status line back |

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

- **The spinner text cannot change during a turn.** It's captured in a `useState`
  initializer when the spinner mounts. `/n` lands on the *next* spinner.
- **No single key can turn the page.** Claude Code's keybindings map to a closed enum of
  built-in actions; there's no "run a command" action to bind. `/n` is the shortest path.
- **The status line has no wall-clock refresh** — it advances per assistant message, so a
  long single tool call holds its line.
- **Subagent panes show the stock verbs.** Task spinners bypass `spinnerVerbs` entirely.
- **An in-progress todo outranks the spinner verb**, so reading pauses during todo work.
- **`disableAllHooks: true` disables the status line too**, and the plugin goes dark.
- **Concurrent sessions** share one global setting and will interleave each other's lines.
  Locking keeps the bookmark consistent, so nothing is lost.
- **Your `settings.json` gets reformatted** on first write. A copy of the original is kept
  at `~/.claude/thinking-book/settings.backup.json`, and `/book off` restores every key
  the plugin touched.

`spinnerVerbs` and `statusLine` are documented settings. Single-element sampling is
inference from the current implementation — if a future release re-picks verbs on a timer,
this degrades to showing nearby lines rather than breaking.

## Development

No dependencies beyond the Python 3 standard library.

```
python3 -m unittest discover -s tests -p "test_*.py"
```

## Licence

MIT
