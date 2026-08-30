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

- Distinguish the desired status-line surface from whether its command is actually
  installed. Today the default config says it is on before `statusLine` exists, so timer
  mode defers to a phantom surface and the first line can repeat forever.
- Install the wrapper after the first successful interactive import when the surface is
  desired, preserving any existing status line exactly as `pane on` does. This makes
  `/book gutenberg ...` or `/book load ...` enough without changing settings merely
  because an empty plugin session started.
- Add `/book on` as an explicit recovery from `/book off`; it resumes, enables both
  surfaces, installs the status line, and syncs the spinner. `/book off` currently has no
  inverse and leaves the spinner disabled permanently through the CLI.
- On `SessionStart`, reconcile the cached "installed" state with the live status-line
  entry before deciding whether the Stop hook or status line owns timer advancement.
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
- Make settings updates compare the before/after dictionaries while holding the lock and
  skip the atomic write when nothing changed. Manual, paused, and within-dwell turns must
  not rewrite `settings.json` or trigger Claude Code's settings watcher.
- When clearing, restore the backup only if the live value is still recognisably the
  plugin's. If the user changed `spinnerVerbs` while thinking-book was active, preserve
  the newer user value rather than overwriting it with an older backup.
- Add end-to-end tests proving exact restoration of user `spinnerVerbs`, `statusLine`,
  unrelated nested values, and malformed-file byte preservation.

### 5. Hot-path redesign and measurement

- Avoid reading stdin unless a wrapped status-line command actually needs it.
- Replace the three `cat | tr` state reads with direct POSIX `read` builtins and validate
  each numeric value in the shell. This removes the pipelines without introducing a
  second cursor representation or a new cross-writer consistency invariant.
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
- Reject obvious PDF, ZIP/Office, Kindle, and other binary input passed to the plaintext
  loader instead of turning replacement-character noise into a successful "book".
- Reconstruct single-path arguments after slash-command blob splitting for `load`,
  `libby`, `clippings`, and `readwise`, so ordinary paths containing spaces work without
  requiring shell syntax knowledge.
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

## Round 2: independent Opus ideas and decisions

Opus independently read the plugin and proposed a v0.4 centered on making the default
path work and removing unnecessary settings/hot-path work. It could not run commands in
its headless sandbox, so its performance claims are treated as hypotheses to benchmark.

Adopted:

- Fix the phantom default status-line state that makes timer mode hold forever before
  `pane on`.
- Skip no-op `settings.json` writes under the existing lock.
- Add `/book on`, the missing inverse of `/book off`.
- Preserve the logical item/offset when queue rebuilds shift global line numbers.
- Use shell builtins for numeric state reads and avoid stdin/output subshells when no
  wrapped status line exists.
- Repair slash-command paths containing spaces, reject binary-as-text imports, and avoid
  overwriting a `spinnerVerbs` value the user changed while the plugin was active.

Partially adopted or modified:

- Opus recommended defaulting the status-line surface off and requiring `/book on`.
  Instead, the first successful interactive import installs the desired default surface.
  This is an explicit reading action, removes an onboarding step, and avoids modifying an
  empty install merely on session start. `/book on` remains available and `pane off`/`off`
  remain authoritative.
- Opus recommended only removing process forks from the current `awk` lookup. That is
  included, but the plan still shards the stream because a lookup at line 25,000 remains
  a near-full scan and the user explicitly set a no-full-file-scan hot-path constraint.
- Opus suggested a Readwise API integration using `READWISE_TOKEN`. The plan keeps local
  CSV/JSON exports instead: no secret storage, pagination, network dependency, or new
  remote failure mode is needed to deliver the requested highlights importer.

Rejected for this version:

- Natural variable pacing based on fragment length: it makes dwell semantics less
  predictable and belongs behind a later opt-in experiment, not a hardening release.
- Status-line percentage/context: useful in the reader pane, but it spends scarce status
  space and changes the minimal reading surface without addressing a core problem.
- Automatic pruning of feed articles, `/book tidy`, Gutenberg search UI, and a prefix
  command: reasonable follow-ups, but lower impact than integrity, bookmarks, importers,
  and the hot path.
- A background wall-clock daemon, JSON parsing in the shell, multi-element spinner verbs,
  SQLite, and DRM-adjacent Kindle scraping/decryption: these conflict with the plugin's
  simplicity, deterministic order, or explicit hard constraints.
