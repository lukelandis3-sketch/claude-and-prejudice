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

Then pick something to read:

```
/book gutenberg moby dick
```

The first import enables an empty status-line slot automatically. If you already have a
status line, thinking-book leaves it alone and tells you to opt in with `/book pane on`,
which runs both. Restart Claude Code once if a newly enabled status line is not visible.

## Reading

Plugin commands are namespaced, so they are `/thinking-book:n`, `/thinking-book:book`, and
so on. That is a lot of keystrokes for a page turn — see *A real `/n`* below.

| Command | What it does |
|---|---|
| `/thinking-book:n` | Turn the page — advance one line |
| `/thinking-book:b` | Back one line |
| `/book status` | Title, author, position, percent read, current line |
| `/book queue` | What's queued, and what you're in the middle of |
| `/book open <id-or-title>` | Switch books; each one keeps its own bookmark |
| `/book mode timer\|turn\|manual` | How pages turn (below) |
| `/book dwell <seconds>` | Reading pace for timer mode (default 8) |
| `/book pause` / `/book resume` | Freeze on a line, or carry on |
| `/book on` / `/book off` | Enable both reading surfaces, or restore the originals |
| `/book pane on\|off` | Attach or detach the status line surface |
| `/book repair` | Undo a self-wrapped status line (see below) |
| `/book version` | Which version is running, and from which directory |
| `/book refresh <secs\|off>` | Set `statusLine.refreshInterval` where your version supports it |

## Turning pages without spending a turn

A slash command is a model turn: an assistant response, latency, tokens, and book-keeping
noise threaded through your actual work. Two better ways, neither of which does that.

```sh
/thinking-book:book install-cli      # symlinks bin/tb into ~/.local/bin
```

| How | Cost |
|---|---|
| `!tb n` inside Claude Code | **No model turn.** A leading `!` is bash mode: it runs locally, and the input and output are recorded as `<bash-input>`/`<bash-stdout>` lines that are not treated as a user message. They do still sit in the conversation and go along with your next prompt. |
| `tb n` in another terminal | **Nothing at all.** No turn, no context. |
| `/thinking-book:n` | A full model turn. Works, but it is the expensive option. |

### The reader pane

```sh
tb reader
```

Run it in a split and you get the one-keypress page turn that is impossible inside Claude
Code itself: **space** or `n` advances, `b` goes back, arrow keys work too, `r` redraws, `q` quits. It shows the line and
your position, updates when something else moves the bookmark (timer mode, `!tb n`), and
writes through to the same position file — so the spinner in the Claude pane follows along on
its next turn. One bookmark, several windows.

Pair it with `/book mode manual`, or lines will keep advancing on the clock underneath you.

### A one-key binding

In tmux, for a page turn that shows you the line without leaving the pane you are in:

```tmux
bind -n F8 run-shell 'tmux display-message "$(tb n)"'
```

Any terminal that can bind a key to a shell command will do the same — `tb n` prints the new
line on stdout, so it composes with whatever your emulator offers.

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
| `/book clippings <My Clippings.txt>` | Your Kindle's local clippings export | Highlights and notes, grouped by book |
| `/book readwise <export.csv\|export.json>` | A Readwise data export | Highlights, grouped by book |

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

`My Clippings.txt` and Readwise imports are offline and contain only excerpts the user
exported. They do not read or decrypt Kindle book files.

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
immutable 256-line shards and publishes them through one atomic generation pointer. The
shell reads at most one shard. On the development Mac it measures 8.82 ms median / 9.62 ms
p90 at line 25,000 of 25,000 over 100 warm invocations, against a 5 s timeout.

State lives in `~/.claude/thinking-book/`. JSON files are the human-readable record;
flat one-value files (`pos`, `last`, `count`, `hot.env`) and immutable stream shards keep
the hot path to shell builtins plus one bounded lookup.

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
  Locking keeps state valid; a status-line page turn can race a queue rebuild by at most
  one line because portable POSIX `sh` cannot take Python's `flock` on macOS.
- **`refreshInterval` may not exist in your version.** Claude Code 2.1.251 has no such key,
  so `/book refresh` is a no-op there; other versions (and tools like ccstatusline) do use
  it. Harmless either way — unknown settings keys are ignored.
- **Your `settings.json` gets reformatted** on first actual change. The original bytes are
  kept at `~/.claude/thinking-book/settings.backup.raw` for v0.4+ installs, and `/book off`
  restores every value the plugin touched, including missing, null, and custom values.
  Malformed settings are never replaced; the command names the file to repair.

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

## Keeping it up to date

Installing from a directory source means the plugin runs straight out of your git checkout,
so it does not update itself — `git pull` in that directory *is* the update, followed by a
restart if `hooks/hooks.json` changed.

If a command errors with `unknown command`, that is the signal: the error names the version
and the directory it ran from, so compare it against `/book version` and this README.

## Development

No dependencies beyond the Python 3 standard library.

```
python3 -m unittest discover -s tests -p "test_*.py"
```

## Licence

MIT
