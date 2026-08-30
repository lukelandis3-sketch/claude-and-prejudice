# Claude & Prejudice

Read a book in the margins of Claude Code—one status line at a time.

This plugin replaces the little words Claude Code shows while it works — *Pondering…*,
*Percolating…*, *Reticulating…* — with successive lines of whatever you're reading. The
persistent reader is the line marked with a book icon below Claude Code's input box.

```
 ✻ Call me Ishmael.… (12s · esc to interrupt)
```

## Install

```
/plugin marketplace add lukelandis3-sketch/claude-and-prejudice
/plugin install thinking-book
```

Start with your own title, URL, or file in one command. The first book automatically
enables the reader at 250 WPM:

```
/thinking-book:book moby dick
/thinking-book:book ~/Books/my-book.epub
```

`/thinking-book:setup` is only for resetting an existing reader to the recommended HUD,
spinner, and 250 WPM defaults. It asks no questions and never retries a download.

For library management and manual controls outside Claude, install the optional local
launcher once. Books and page turns can then stay entirely outside the transcript:

```sh
book ~/Books/next.epub             # zero model tokens or conversation context
book "Pride and Prejudice"         # title search from a separate terminal
```

Or start directly. The plugin detects Gutenberg searches, URLs, EPUB/text files, Kindle
clippings, Readwise exports, and Libby exports:

```
/thinking-book:book moby dick
/thinking-book:book ~/Books/my-book.epub
```

The first import enables an empty status-line slot automatically. If you already have a
status line, thinking-book leaves it alone and tells you to opt in with
`/thinking-book:book display hud`,
which runs both. Restart Claude Code once if a newly enabled status line is not visible.

## Reading

Read from the line marked `📖` **below the input box**. The spinner is a secondary preview
while Claude works; command output in the transcript is history, not the reading surface.
At the default 250 WPM, pages turn automatically as Claude Code refreshes the status line.

Plugin commands are namespaced, so they are `/thinking-book:n`, `/thinking-book:book`, and
so on. That is a lot of keystrokes for a page turn — see *A real `/n`* below.

| Command | What it does |
|---|---|
| `/thinking-book:n` | Turn the page — advance one line |
| `/thinking-book:b` | Back one line |
| `/thinking-book:setup <book>` | Reset reading to HUD, spinner, and 250 WPM defaults |
| `/thinking-book:book` | Compact reading dashboard and controls |
| `/thinking-book:book status` | Dashboard plus display details and the whole queue |
| `/thinking-book:book queue` | A numbered library with each book's bookmark |
| `/thinking-book:book open <number-or-title>` | Switch books; each one keeps its own bookmark |
| `/thinking-book:book queue rm <number-or-title>` | Remove a book and continue at the next one |
| `/thinking-book:book mode timer\|turn\|manual` | How pages turn (below) |
| `/thinking-book:book pace <wpm>` | Set the timer's reading speed (default 250 WPM) |
| `/thinking-book:book pause` / `/thinking-book:book resume` | Freeze on a line, or carry on |
| `/thinking-book:book display hud\|line\|spinner\|off` | Choose where the book appears |
| `/thinking-book:book on` / `/thinking-book:book off` | Enable reading, or restore every setting it touched |
| `/thinking-book:book repair` | Repair a moved or self-wrapped status line |
| `/thinking-book:book version` | Which version is running, and from which directory |
| `/thinking-book:book refresh <secs\|off>` | Set `statusLine.refreshInterval` where supported |

### Graphical reading HUD

`/thinking-book:book display hud` adds a precomputed progress row above the prose:

```
Moby-Dick · ████░░░░░░ 124/310 (40%) · 250 wpm
📖 Call me Ishmael.
```

It is optional and off by default. Normal mode retains the original one-line display and
one bounded lookup, and does not generate HUD shards. Enabling the HUD adds matching
256-line metadata shards without republishing the prose; each display then performs one
additional bounded lookup. It never starts Python or scans the book. Switch back with
`/thinking-book:book display line`, use only the live spinner with
`/thinking-book:book display spinner`, or restore with `/thinking-book:book display off`.

The HUD is display-only. The `/thinking-book:book` dashboard identifies the reading surface and explains
the active page-turn mode. It does not steer timer-mode readers into shell commands that
clutter the transcript.

## Local reading without spending a turn

A slash command is a model turn: an assistant response, latency, tokens, and book-keeping
noise threaded through your actual work. Install the local `book` launcher once:

```sh
/thinking-book:book install-cli      # symlinks bin/book into ~/.local/bin
```

| How | Cost |
|---|---|
| `book <title\|url\|file>` in another terminal | **Literally zero model tokens and no conversation context.** Import and setup run locally. |
| `book next` / `book back` in another terminal | **Nothing at all.** No turn, no context. |
| `/thinking-book:n` | A full model turn. Works, but it is the expensive option. |

Claude Code's `!` shell mode records its command and output in the transcript and may lead
Claude to respond, as shown in real use. It is therefore not presented as a reading control.
The old `tb` command remains a compatibility alias for existing installs and hotkeys.

The plugin commands are user-invocable only: their descriptions are not injected into
Claude's always-on model context. Merely installing Claude & Prejudice costs no model tokens.

### The reader pane

```sh
book reader
```

Run it in a split and you get the one-keypress page turn that is impossible inside Claude
Code itself: **space** or `n` advances, `b` goes back, arrow keys work too, `r` redraws, `q` quits. It shows the line and
your position, updates when something else moves the bookmark (timer mode or `book next`), and
writes through to the same position file — so the spinner in the Claude pane follows along on
its next turn. One bookmark, several windows.

Pair it with `/thinking-book:book mode manual`, or lines will keep advancing underneath you.

### A one-key binding

In tmux, for a page turn that shows you the line without leaving the pane you are in:

```tmux
bind -n F8 run-shell 'tmux display-message "$(book next)"'
```

Any terminal that can bind a key to a shell command will do the same — `book next` prints the new
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
allowed-tools: Bash(book:*)
---
!`book next`
EOF
```

This uses the stable local launcher, so plugin updates cannot invalidate the command path.

### Advance modes

- **`timer`** (default) — each fragment stays up for its word count at 250 WPM, with a
  two-second minimum for short headings. It advances at most once per status-line refresh,
  so quiet turns read slower than the nominal pace and walking away never skips hundreds
  of lines. `/thinking-book:book pace <wpm>` adjusts it;
  `/thinking-book:book dwell <seconds>` remains available for a
  fixed interval.

Existing installations keep their fixed-second pace until you run
`/thinking-book:book pace 250` or
start a new book through setup.
- **`turn`** — exactly one line per assistant turn, regardless of how long it took.
- **`manual`** — the line holds until you type `/n`.

## What you can read

| Command | Source | Gives you |
|---|---|---|
| `/thinking-book:book <title\|url\|file>` | Auto-detected source | Books, articles, or highlights, opened immediately |
| `/thinking-book:book add <title\|url\|file>` | Auto-detected source | Books, articles, or highlights, queued for later |
| `/thinking-book:book feed add <url>` | An RSS or Atom feed | New articles, queued automatically |

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
  the book. `/thinking-book:book add` reads that file.

**This plugin does not strip DRM** — not Kindle KFX, not Adobe DRM, not Apple Books. An
encrypted EPUB is rejected with an explanation rather than worked around. For sequential
prose, use DRM-free copies (Standard Ebooks, No Starch, O'Reilly, most Humble bundles),
the public domain, or the open web.

`My Clippings.txt` and Readwise imports are offline and contain only excerpts the user
exported. They do not read or decrypt Kindle book files. The standard filename is also
recognized automatically by `/thinking-book:book load <path/to/My Clippings.txt>`.

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
Across 200 invocations on the development Mac at line 25,000 of 25,000, compact manual mode
measured 6.95 ms median (7.43 ms p95); the 250 WPM timer measured 9.55 ms median
(10.74 ms p95), and the graphical HUD measured 11.79 ms median (12.89 ms p95), against a
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
- **No plugin keybinding can turn the page.** Claude Code's keybindings map to a closed
  enum of built-in actions. Use automatic pacing, `book reader`, or a terminal hotkey.
- **The status line has no wall-clock refresh** — it advances per assistant message, so a
  long single tool call holds its line.
- **Subagent panes show the stock verbs.** Task spinners bypass `spinnerVerbs` entirely.
- **An in-progress todo outranks the spinner verb**, so reading pauses during todo work.
- **`disableAllHooks: true` disables the status line too**, and the plugin goes dark.
- **Concurrent sessions** share one global setting and can interleave lines. A generation
  check prevents an old status-line process from writing its cursor into a rebuilt queue.
- **`refreshInterval` may not exist in your version.** Claude Code 2.1.251 has no such key,
  so `/thinking-book:book refresh` is a no-op there; other versions do use
  it. Harmless either way — unknown settings keys are ignored.
- **Your `settings.json` gets reformatted** on first actual change. The original bytes are
  kept at `~/.claude/thinking-book/settings.backup.raw`, and `/thinking-book:book off`
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
/thinking-book:book repair
```

0.2 makes this impossible three ways over: an exported `TB_IN_STATUSLINE` guard that stops
recursion at the first level, path-independent identity so `pane on` recognises its own
script wherever it was installed from, and `repair` to unwind machines already in that state.

Unlike most status line tools — [ccstatusline](https://github.com/sirmalloc/ccstatusline) and
friends simply overwrite `statusLine` — thinking-book wraps whatever you already had and runs
it alongside the book. `/thinking-book:book off` puts it back exactly as it was.

Before uninstalling, run `/thinking-book:book off` so Claude's settings are restored while
the plugin is still present.

## Keeping it up to date

Installing from a directory source means the plugin runs straight out of your git checkout,
so it does not update itself — `git pull` in that directory *is* the update, followed by a
restart if `hooks/hooks.json` changed.

If a command-shaped typo errors with `unknown command`, the message names the version and
directory it ran from. Compare those against `/thinking-book:book version` and this README.

## Development

No dependencies beyond the Python 3 standard library.

```
python3 -m unittest discover -s tests -p "test_*.py"
```

## Licence

MIT
