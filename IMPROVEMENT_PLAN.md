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
  desired **and no status line exists**. This makes `/book gutenberg ...` or `/book load
  ...` enough for the default case without silently taking over a third-party surface.
  When another status line exists, explain that `/book pane on` will wrap it explicitly.
  Gate this in interactive command handlers, never `_install`, so a detached feed refresh
  cannot change settings.
- Add `/book on` as an explicit recovery from `/book off`; it resumes, enables both
  surfaces, installs the status line, and syncs the spinner. `/book off` currently has no
  inverse and leaves the spinner disabled permanently through the CLI.
- On `SessionStart`, clear a per-session status-line liveness marker. `statusline.sh`
  creates it on its first invocation in the session. The Stop hook defers timer advancing
  only after that marker proves the surface has actually run; a `statusLine` settings
  entry alone is insufficient because initial activation may require a restart.
- Extract one status-line enabling path used by `pane on`, `/book on`, and automatic
  activation. Keep read/decide/write under the settings lock, define a consistent lock
  order for settings/config state, and test concurrent activation cannot self-wrap or
  lose the original third-party command.
- Sync the spinner immediately after a successful import, so interactive imports show
  the first line without waiting for another session or turn.
- Keep `pane off` and `/book off` authoritative: automatic activation must not re-enable
  a surface the user disabled.

### 2. Library selection and per-item bookmarks

- Add a small bookmark store keyed by item id. Record a relative line within each item,
  not a global stream position, so ordinary queue edits preserve it.
- Add `/book open <id-or-unambiguous-title>` to save the current item bookmark and jump
  to the selected item's saved position. Make ambiguous matches explain the available
  choices.
- Make `queue rm` and `queue clear` preserve a sensible logical position and never leave
  the bookmark beyond the rebuilt stream.
- Remap position inside the Python state lock: capture `(item_id, offset)` against the old
  index, rebuild, resolve against the new index, then write `pos`. Apply the same path to
  re-imports, which can change an earlier item's length. The lock-free shell may still
  interleave a single timer step; the honest concurrency guarantee is at most one line of
  cursor slip, never corruption.
- Reset the dwell clock after `open` or a queue edit so the selected line remains visible
  for a full dwell. Matching is deterministic: exact id, then unique case-insensitive
  title substring, otherwise list candidates.
- Show saved progress in `queue`/`status`. Keep the existing flat stream and one global
  active cursor so hooks and the reader pane stay simple.

### 3. Legitimate highlights importers

- Add `/book clippings <My Clippings.txt>` for Kindle's user-exported highlights. Parse
  the de-facto delimiter-based text defensively, preserve file order, de-duplicate exact
  repeated highlights, and ignore localized metadata/bookmark records without prose.
- Add `/book readwise <export.csv|export.json>` for user-exported Readwise highlights.
  Use only `csv`/`json` from the standard library; accept explicit aliases
  (`Highlight`/`text`, `Book Title`/`title`, `Author`/`author`, `Note`/`note`, and
  `Location`/`location`); group records by book; and create stable queue items.
- Install multi-book exports as a batch: save every item, extend the queue once, rebuild
  once, and print one summary. Stable ids derive from source kind plus normalized title
  and author, so a re-export under a new filename updates rather than duplicates books.
- Label both sources as `highlights`, explain that they import excerpts rather than full
  books, and keep the README's no-DRM boundary explicit.

### 4. Settings integrity hardening

- Preserve the original `settings.json` bytes in a new `settings.backup.raw` artifact
  before the first v0.4 mutation, alongside metadata recording validity and source path.
  Keep legacy `settings.backup.json` for existing key restoration; do not repurpose it or
  claim byte fidelity for pre-v0.4 backups. For valid JSON, continue surgical
  read-modify-write under `flock` and preserve every unrelated key.
- If the live file is malformed or not a JSON object, do not replace it. Interactive
  commands should explain the exact file that needs repair; hook commands should catch
  the error, exit 0, and print nothing under `--quiet`.
- Track original presence/value explicitly for both `spinnerVerbs` and `statusLine`, so
  absent, `null`, empty/falsy, and ordinary values all restore exactly.
- Make settings updates compare the before/after dictionaries while holding the lock and
  skip the atomic write when nothing changed. Manual, paused, and within-dwell turns must
  not rewrite `settings.json` or trigger Claude Code's settings watcher.
- Record the exact spinner value last written. Restore the backup only when the live value
  still equals that record; if the user changed it after the plugin's last write, preserve
  the newer value. This cannot prevent an active Stop hook from replacing an edit before
  `/book off` runs, so the promise is deliberately limited to edits after the last write.
- Add end-to-end tests proving exact restoration of user `spinnerVerbs`, `statusLine`,
  unrelated nested values, and malformed-file byte preservation.

### 5. Hot-path redesign and measurement

- Avoid storing stdin unless a wrapped status-line command needs it. Otherwise drain it
  without retaining the JSON so large input cannot cause EPIPE or hang the caller.
- Replace the three `cat | tr` state reads with direct POSIX `read` builtins and validate
  each numeric value in the shell. This removes the pipelines without introducing a
  second cursor representation or a new cross-writer consistency invariant.
- First benchmark the builtin/fork reduction. Stream sharding is a go/no-go after that
  measurement. If still warranted to meet the no-full-scan constraint, build immutable
  fixed-size shards under a generation id and atomically publish one generation pointer
  last; the status line reads only that generation. Do not create one entry per fragment,
  and use the same indexed lookup from the Python reader.
- Preserve all three recursion protections: exported `TB_IN_STATUSLINE`, path-independent
  identity including paths with spaces, and `/book repair`.
- Benchmark at line 25,000 of 25,000 over at least 100 invocations before and after,
  recording median and p90 on the same named host; require a non-worse median and retain
  the suite's generous platform-independent timeout bound. Add
  behavioral tests for shard boundaries, missing/corrupt snapshots, wrapped stdin, timer
  advancement, and silent exit-zero failures. The acceptance criterion is no regression;
  the target is a material improvement over the measured local baseline.

### 6. Errors, help, and feel

- Turn raw network and parser exception labels into short messages that name the failed
  source and suggest a next action. Reject over-limit downloads rather than silently
  importing truncated content, and cap decompressed gzip data as well as wire bytes.
- Reject obvious PDF, ZIP/Office, Kindle, and other binary input passed to the plaintext
  loader instead of turning replacement-character noise into a successful "book".
- Preserve the raw remainder of a one-blob slash command for `load`, `libby`, `clippings`,
  and `readwise` before generic `shlex` splitting/stray-command filtering. Direct shell
  argv paths still work; slash paths keep spaces and colons without weakening the pasted
  second-command guard for other verbs.
- Make bare `/book` output task-oriented help with a shortest-path first command.
- Keep advanced controls discoverable but out of the first-run path. Update command
  hints and examples for `open`, clippings, and Readwise.
- Keep all hook paths best-effort, exit 0, and silent with `--quiet`.

## Verification gates

- Existing tests remain green except one intentional behavior inversion:
  `test_corrupt_settings_file_is_backed_up_not_propagated` currently requires replacing
  malformed settings and will be rewritten to require byte preservation/refusal. New
  behavior uses `unittest` regression coverage and standard-library-only fixtures.
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

## Round 3: Opus review revisions

The merged-plan review found several cross-feature hazards and one explicit conflict with
the existing suite. The plan now adopts these corrections:

- Timer ownership uses observed per-session status-line liveness, not a settings entry.
  Required dual tests cover a configured-but-never-run surface (Stop owns the clock) and
  a surface that ran in this session (status line owns it, with no double advance).
- Automatic activation never wraps a third-party status line and is unreachable from
  feed refresh. Imports after `/book off` stay paused and tell the user to run `/book on`.
- A single locked enabling path prevents concurrent sessions from nesting the wrapper.
  Config read-modify-write operations also move under the state lock with a documented
  lock order; helpers already inside that lock must not acquire it recursively.
- Malformed settings refusal deliberately replaces one legacy test that required unsafe
  recovery. Raw backup uses a new artifact so existing JSON backup semantics migrate
  honestly. Restoration tests cover both touched keys across absent, null, falsy, and
  ordinary original values.
- Logical-position remapping happens inside the queue/install lock and includes re-import,
  while acknowledging the shell can interleave one page turn because POSIX `flock` is not
  portable to macOS. `open` and queue edits reset dwell.
- Multi-book import uses one rebuild and stable content-identity ids. Clippings fixtures
  cover BOM, CRLF, localized metadata, bookmarks, exact duplicates, and multiple books;
  Readwise fixtures cover every named column alias and both CSV/JSON.
- Plaintext sniffing distinguishes a misnamed valid EPUB ZIP (route it to the EPUB loader)
  from Office/ZIP, PDF, `BOOKMOBI`, and KFX input (reject with explanation). Network tests
  require explicit errors for wire data over the cap and bounded gzip expansion.
- Hot-path stdin tests include 1 MB with and without a wrapper. Numeric reads suppress
  missing-file/EOF diagnostics under `set -u`. Index tests, if sharding proceeds, cover
  generation boundaries, corrupt/missing pointers, `pos >= count`, and safe silence.

Rejected or cut after review:

- Hook-command shortening is cut unless direct measurement proves hook command strings
  consume always-on model context. The benefit is unverified and changing hooks imposes a
  restart on upgrades.
- A write-on-every-invocation heartbeat is rejected for hot-path cost. A SessionStart-cleared
  marker written on the first observed status-line invocation provides the ownership fact
  needed without continuous disk churn.
- A `dd` byte-offset index is not selected up front: two extra processes may cost more
  than one bounded shard lookup. Fork reduction is measured first, then any indexed design
  must beat the baseline and publish atomically.

Implementation remains in separately reviewable commits: liveness/activation plumbing;
settings integrity; hot-path process removal and conditional indexing; logical bookmarks;
Clippings batch import; Readwise import; source/path errors and limits; then help, version,
and documentation. Each commit runs the complete suite before the next begins.
