# Claude & Prejudice

Read a book in the margins of Claude Code—one status line at a time.

This plugin replaces the little words Claude Code shows while it works — *Pondering…*,
*Percolating…*, *Reticulating…* — with successive lines of whatever you're reading, and
puts the same line in your status line, where it can turn pages during a turn.

```
 ✻ Call me Ishmael.… (12s · esc to interrupt)
```

## Install

```
/plugin marketplace add lukelandis3-sketch/claude-and-prejudice
/plugin install thinking-book
```

Start with your own title, URL, or file in one command. HUD, spinner, and 250 WPM are
configured automatically:

```
/thinking-book:setup moby dick
/thinking-book:setup ~/Books/my-book.epub
```

Run `/thinking-book:setup` without an argument to resume, or to see the syntax when no
book is queued. Setup asks no questions, offers no presets, and never retries a download.

The first successful setup points to the optional one-time local controls. After that,
books and page turns can avoid model turns entirely:

```sh
tb ~/Books/next.epub             # separate terminal: zero model tokens or conversation context
!tb "Pride and Prejudice" # inside Claude Code: no model turn; output may enter later context
```

Or start directly. The plugin detects Gutenberg searches, URLs, EPUB/text files, Kindle
clippings, Readwise exports, and Libby exports:

```
/book moby dick
/book ~/Books/my-book.epub
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
| `/thinking-book:setup <book>` | Start your book with HUD, spinner, and 250 WPM |
| `/book` | Compact reading dashboard and controls |
| `/book status` | Dashboard plus display details and the whole queue |
| `/book queue` | A numbered library with each book's bookmark |
| `/book open <number-or-title>` | Switch books; each one keeps its own bookmark |
| `/book queue rm <number-or-title>` | Remove a book and continue at the next one |
| `/book mode timer\|turn\|manual` | How pages turn (below) |
| `/book pace <wpm>` | Set the timer's reading speed (default 250 WPM) |
| `/book pause` / `/book resume` | Freeze on a line, or carry on |
| `/book display hud\|line\|spinner\|off` | Choose where and how the book appears |
| `/book on` / `/book off` | Enable reading, or restore every setting it touched |
| `/book repair` | Undo a self-wrapped status line (see below) |
| `/book version` | Which version is running, and from which directory |
| `/book refresh <secs\|off>` | Set `statusLine.refreshInterval` where your version supports it |

### Graphical reading HUD

`/book display hud` adds a precomputed progress row above the prose:

```
📖 Moby-Dick · ████░░░░░░ 124/310 (40%) · 250 wpm
Call me Ishmael.
```

It is optional and off by default. Normal mode retains the original one-line display and
one bounded lookup, and does not generate HUD shards. Enabling the HUD adds matching
256-line metadata shards without republishing the prose; each display then performs one
additional bounded lookup. It never starts Python or scans the book. Switch back with
`/book display line`, use only the live spinner with `/book display spinner`, or restore with
`/book display off`.

The HUD is display-only. The `/book` dashboard prints working in-app page controls. When
`tb` is installed they are `!tb n` and `!tb b`; otherwise it shows the namespaced controls
and the one-time `/book install-cli` shortcut.

## Local reading without spending a turn

A slash command is a model turn: an assistant response, latency, tokens, and book-keeping
noise threaded through your actual work. Install the local `tb` launcher once:

```sh
/thinking-book:book install-cli      # symlinks bin/tb into ~/.local/bin
```

| How | Cost |
|---|---|
| `tb <book>` in another terminal | **Literally zero model tokens and no conversation context.** Import and setup run locally. |
| `!tb <book>` inside Claude Code | **No model turn.** Its short Bash transcript may accompany a later prompt. |
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

- **`timer`** (default) — each fragment stays up for its word count at 250 WPM, with a
  two-second minimum for short headings. It advances at most once per status-line refresh,
  so quiet turns read slower than the nominal pace and walking away never skips hundreds
  of lines. `/book pace <wpm>` adjusts it; `/book dwell <seconds>` remains available for a
  fixed interval.

Existing installations keep their fixed-second pace until you run `/book pace 250` or
start a new book through setup.
- **`turn`** — exactly one line per assistant turn, regardless of how long it took.
- **`manual`** — the line holds until you type `/n`.

## What you can read

| Command | Source | Gives you |
|---|---|---|
| `/book <title\|url\|file>` | Auto-detected source | Books, articles, or highlights, opened immediately |
| `/book add <title\|url\|file>` | Auto-detected source | Books, articles, or highlights, queued for later |
| `/book feed add <url>` | An RSS or Atom feed | New articles, queued automatically |

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
  the book. `/book add` reads that file.

**This plugin does not strip DRM** — not Kindle KFX, not Adobe DRM, not Apple Books. An
encrypted EPUB is rejected with an explanation rather than worked around. For sequential
prose, use DRM-free copies (Standard Ebooks, No Starch, O'Reilly, most Humble bundles),
the public domain, or the open web.

`My Clippings.txt` and Readwise imports are offline and contain only excerpts the user
exported. They do not read or decrypt Kindle book files. The standard filename is also
recognized automatically by `/book load <path/to/My Clippings.txt>`.

## How it works

Claude Code has a documented `spinnerVerbs` setting:

```json
{ "spinnerVerbs": { "mode": "replace", "verbs": ["Call me Ishmael."] } }
```

It picks from that list **at random, once, when the spinner mounts**. So the plugin writes
a *single-element* list — random-of-one is deterministic — and rewrites it as you read.
Settings files are watched, so the change lands without a restart.

The status line is the livelier surface. Claude Code re-runs a `statusLine` command **once
per assistant message**, so it can update several times inside a single turn. `statusline.sh`
is the hot path: no Python, no JSON parsing, no full-file scans — Python pre-chunks
passages to a readable 100-character maximum, stores immutable 256-line prose shards, and
publishes them through one atomic generation pointer. Optional HUD metadata shards are
added atomically beside them. The shell reads one prose shard and, only when the HUD is
enabled, one matching metadata shard. Longer passages from older libraries wrap at the
terminal edge instead of being clipped, while normal lines keep the same fast path.
Across 100 invocations on the development Mac at line 25,000 of 25,000, compact manual mode
measured 7.12 ms median (8.82 ms p95); the 250 WPM timer measured 9.31 ms median
(10.71 ms p95), and the graphical HUD measured 11.53 ms median (13.14 ms p95), against a
5 s timeout.

State lives in `~/.claude/thinking-book/`. JSON files are the human-readable record;
flat one-value files (`pos`, `last`, `hot.env`) and self-contained immutable stream
generations keep the hot path to shell builtins plus one bounded lookup. A 25,000-fragment
stream rebuild measured 34 ms; the book is not duplicated into a second full-stream cache.

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

If a command-shaped typo errors with `unknown command`, the message names the version and
directory it ran from. Compare those against `/book version` and this README.

## Development

No dependencies beyond the Python 3 standard library.

```
python3 -m unittest discover -s tests -p "test_*.py"
```

## Licence

MIT
