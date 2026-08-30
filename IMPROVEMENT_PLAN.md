# thinking-book v0.4 improvement plan

This plan follows a complete read of the v0.3.0 plugin and its 154 tests. It is a
working contract for the implementation and review rounds, not a list of every idea
that could fit in the plugin.

## Goals

1. Make the first useful reading experience one command after installation.
2. Make several books practical: each item keeps its own bookmark and can be opened
   directly without losing the reader's place in the others.
3. Add legitimate highlight sources without touching DRM: Kindle `My Clippings.txt`
   and Readwise CSV/JSON exports.
4. Strengthen the promise that `settings.json` is never damaged.
5. Reduce the status-line hot path below the current end-of-stream baseline while
   keeping it POSIX `sh`, dependency-free, silent on failure, and comfortably inside
   Claude Code's five-second deadline.
6. Improve errors and documentation without increasing always-on prompt overhead.

## Planned work

### 1. Safe, automatic activation

- On `SessionStart`, install or refresh the thinking-book status-line wrapper when the
  saved configuration says the surface is on. Preserve and continue to run any existing
  status line exactly as `pane on` does today. This removes the separate `pane on` step;
  after plugin installation/restart, `/book gutenberg ...` or `/book load ...` is enough.
- Sync the spinner immediately after a successful import, so interactive imports show
  the first line without waiting for another session or turn.
- Keep `pane off` and `/book off` authoritative: automatic activation must not re-enable
  a surface the user disabled.
- Shorten hook commands where doing so is compatible with Claude Code, to reduce their
  always-on representation, and document the measured before/after size rather than
  guessing at token counts.

### 2. Library selection and per-item bookmarks

- Add a small bookmark store keyed by item id. Record a relative line within each item,
  not a global stream position, so queue edits cannot invalidate it.
- Add `/book open <id-or-unambiguous-title>` to save the current item bookmark and jump
  to the selected item's saved position. Make ambiguous matches explain the available
  choices.
- Make `queue rm` and `queue clear` preserve a sensible logical position and never leave
  the bookmark beyond the rebuilt stream.
- Show saved progress in `queue`/`status`. Keep the existing flat stream and one global
  active cursor so hooks and the reader pane stay simple.

### 3. Legitimate highlights importers

- Add `/book clippings <My Clippings.txt>` for Kindle's user-exported highlights. Parse
  the documented delimiter-based text defensively, preserve file order, de-duplicate
  repeated highlights, and ignore bookmarks without prose.
- Add `/book readwise <export.csv|export.json>` for user-exported Readwise highlights.
  Use only `csv`/`json` from the standard library, tolerate common export column names,
  group records by book, and create stable queue items.
- Label both sources as `highlights`, explain that they import excerpts rather than full
  books, and keep the README's no-DRM boundary explicit.

### 4. Settings integrity hardening

- Preserve the original `settings.json` bytes in the one-time backup before the first
  plugin mutation. For valid JSON, continue surgical read-modify-write under `flock` and
  preserve every unrelated key.
- If the live file is malformed or not a JSON object, do not replace it. Interactive
  commands should explain the exact file that needs repair; hook commands should catch
  the error, exit 0, and print nothing under `--quiet`.
- Track the original presence/value of each touched key explicitly so an original JSON
  `null` value is distinguishable from a missing key during `/book off`.
- Add end-to-end tests proving exact restoration of user `spinnerVerbs`, `statusLine`,
  unrelated nested values, and malformed-file byte preservation.

### 5. Hot-path redesign and measurement

- Avoid reading stdin unless a wrapped status-line command actually needs it.
- Replace the three `cat | tr` state reads with one shell-builtin-readable snapshot,
  written atomically by Python and by the timer advance path.
- Build fixed-size stream shards at queue-rebuild time. Look up at most one small shard
  instead of scanning from line 1 to line 25,000 on every invocation. Do not create one
  filesystem entry per fragment.
- Preserve all three recursion protections: exported `TB_IN_STATUSLINE`, path-independent
  identity including paths with spaces, and `/book repair`.
- Benchmark at line 25,000 of 25,000 over at least 100 invocations before and after. Add
  behavioral tests for shard boundaries, missing/corrupt snapshots, wrapped stdin, timer
  advancement, and silent exit-zero failures. The acceptance criterion is no regression;
  the target is a material improvement over the measured local baseline.

### 6. Errors, help, and feel

- Turn raw network and parser exception labels into short messages that name the failed
  source and suggest a next action. Reject over-limit downloads rather than silently
  importing truncated content, and cap decompressed gzip data as well as wire bytes.
- Make bare `/book` output task-oriented help with a shortest-path first command.
- Keep advanced controls discoverable but out of the first-run path. Update command
  hints and examples for `open`, clippings, and Readwise.
- Keep all hook paths best-effort, exit 0, and silent with `--quiet`.

## Verification gates

- Existing 154 tests remain green throughout; new behavior uses `unittest` regression
  coverage and standard-library-only fixtures.
- Every correctness bug found in the dedicated bug round gets a failing reproduction
  test committed before its fix.
- Run shell syntax checks, the complete test suite, focused settings restoration tests,
  importer fixtures, and the 25,000-line status-line benchmark.
- Ask Claude Opus independently for ideas, then for plan review, implementation review,
  and an independent correctness hunt. Record adopted and rejected suggestions with
  reasons in the round report.
- Do not claim a live Gutenberg fetch works if the environment cannot reach Gutendex.
