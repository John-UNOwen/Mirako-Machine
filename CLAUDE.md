# Working in this repo

A Umamusume auto-train bot: a screen-driven state machine (`identify_screen` ->
`HANDLERS[screen]`) fed by template matching, wrapped in a task queue, with a React
web UI in the same process. ADB is the priority platform; the Steam/desktop path is
kept working but does not set the design.

The notes below are the things that are **not** visible from reading the code, and
each one has cost real debugging time at least once.

## Running things

- Use `.venv\Scripts\python.exe`, never a bare `python`.
- Set `PYTHONIOENCODING=utf-8`. Several devtools print `☆` and other non-ASCII and
  die with `UnicodeEncodeError` on a stock PowerShell console under cp1252.
- After **any** edit under `web/src`, run `npm run build` in `web/`. The tracked
  `web/dist` is what the server actually serves, so skipping the build means the
  change silently does not exist.
- Static UI changes appear on a browser refresh. New Python routes do **not** — the
  process has to be restarted. A UI button that 405s is almost always this.

## Adding a config key

A key needs to land in **five** places or it is silently unreachable:

1. `config.template.json` — tracked, the schema of record
2. `config.json` — the live config
3. `config/default.json` — the preset
4. The Zod schema (`web/src/types/*.type.ts`), **plus** a control in the matching
   `web/src/components/` section
5. `core/config.py`'s `reload_config()` — a `load_var(...)` line

Each omission fails differently, and none of them fails loudly:

- Miss the **Zod schema** and the key parses, has no UI, and is erased the next time
  someone saves from the web UI.
- Miss **`reload_config`** and every `getattr(config, "NAME", default)` in the
  codebase quietly returns the default. The UI writes the value to disk, the toggle
  looks right, and nothing reads it back — which presents as a UI bug and is not one.
  This is what happened to the daily-race settings.

`devtools/check_config_keys.py` catches the fifth case. Run it after adding a key.

Instance configs (`config/instances/<name>.json`, written when `--instance` is used) do
**not** add a sixth place. `update_config()` runs against whichever file the process owns
at startup and copies in any template key it lacks, so they heal themselves the next time
each one starts. `core/config.py`'s `load_config()` also layers the template underneath at
read time, so a key missing from a hand-edited file takes a default rather than raising.

Note that `config.json` and `config/default.json` are both gitignored, so a fresh
clone cannot show you this pattern — hence this section.

## Removing a config key

The same five places in reverse, **plus a sixth**: add it to `update_config.RETIRED_KEYS`
(or `RETIRED_NESTED` for a sub-key). The startup heal keeps any key the template does
not know — deliberately, so a hand-added key or a newer build's key survives a rollback
— which means taking a key out of the template leaves it in every existing file forever.

Deleting the code that reads a setting is not removing the setting. The career mode's
code went in September 2026 and its schema did not: 35 dead keys, more than half of
every config, held in place by the template, the heal, a Zod schema that declared them
required, a `reload_config` that hard-indexed them, and a log filter that hid them. Two
had Setup-page controls that did nothing. `devtools/check_config_readers.py` fails on a
loaded variable nothing reads and on a template key nothing loads.

## Coordinates

Constants are authored in **desktop frame** — the Steam window, where the game window is
the 800-wide box at desktop x=155, so `GAME_WINDOW_BBOX = (155, 0, 955, 1080)` is the
**authored** value, not an ADB one. `adjust_constants_x_coords` moves them to the frame
its argument names: `-155` gives the **ADB** frame, where the same constant reads
`(0, 0, 800, 1080)` because ADB captures the 800-wide game area alone, and the `+405`
default gives the
BlueStacks window frame, `(560, 0, 1360, 1080)`. Clipping an 800-wide ADB screenshot to
the authored value would take x 155..955 — off the end of the frame.

Rebasing is **repeatable, not one-shot**. The module remembers the offset currently baked
into the constants (`_APPLIED_OFFSET`) and each call shifts them by the difference, so a
process that asks for a frame gets that frame, whatever frame the constants were in before.
Two frames are named in the code: -155 on the ADB path (`main.py:83`) and the default +405
on the `config.WINDOW_NAME` fallback (`main.py:108`).

**One process reaches both of those frames, because one process runs the bot more than
once.** The two call sites are mutually exclusive inside a single `focus_umamusume()` —
the ADB branch returns at `main.py:84` — but the latch this replaced guarded the
*process*, and the runs share the process while the call sites do not. `start_bot()`
spawns a fresh `threading.Thread(target=main)` on every press while `bot.is_bot_running`
is false (`main.py:350-372`), the hotkey loop (`main.py:392-402`) and the web UI's start
button (`main.py:451-458`) both drive it, and each run re-reads the switch on its way in:
`_run_bot()` calls `config.reload_config()` and `resolve_device()` (`main.py:208-213`)
before `bot.use_adb = config.USE_ADB` (`main.py:171`) — which is what
`server/main.py:1099-1111` tells the user: an ADB switch flipped in the UI governs the
**next** launch. So run 1 takes the window fallback and asks for +405 (`main.py:108`), the
user switches ADB on, and run 2 asks for -155 down the ADB branch (`main.py:83`). The same
pair the other way round is one ADB run followed by one window-fallback run. Whichever run
came first, the second run's offset is the frame the constants have to end in; under the
old one-shot latch that call was dropped and every coordinate stayed 560px out of the
frame the running bot asked for (405 − (−155) = 560) — wrong clicks through a run that
looks perfectly normal, which is what this fix is for. `devtools/check_constant_frames.py`
pins both orders.

Asking for the offset the constants are already in is a no-op, which is what every caller
that rebases once at startup still gets. Do **not** put a one-shot latch back here: a latch
records that *some* rebase happened, never which frame is baked in, so a later call naming
the other frame is dropped and the constants go on describing the first one.

The name suffixes are **behaviour, not documentation**: the function walks module
globals and rebases one whose name ends `_REGION` (a 4-tuple), `_MOUSE_POS` or `_POS`
(a 2-tuple), `_BBOX` (a 4-tuple, *both* x ends), `_BBOXES` (a dict) or `_X` (a dict) —
five families, and the suffix alone is not the whole test: the value has to be of that
shape too. That is why `INDEPENDENT_FOCUS_RADIO_X` and `INDEPENDENT_APTITUDE_COL_X`
move with the frame while `INDEPENDENT_DECK_DOT_FIRST_X = 9`, an int, never does. The
two dict families test their *entries* one by one as well: the ints of a `..._X` dict
and the boxes of a `..._BBOXES` dict move, and an entry of any other shape — a
placeholder, a box of the wrong length, a label — is carried through the rebase
untouched rather than dropped. A rebase runs every time the frame changes, so a walk
that discarded what it did not understand would leave the constant silently one key
short. A constant matching neither half is never shifted, and a relative distance must
therefore *avoid* those suffixes **in those shapes** — a dict of ints called `..._X` or
a dict of boxes called `..._BBOXES` is rebased entry by entry, exactly like the
coordinate beside it, whereas `_OFFSET`, `_PITCH` and `_COUNT` are not looked at. See
`_OFFSET` in `utils/constants.py`, named that way deliberately, as is the rebase's own
`_APPLIED_OFFSET`.
`devtools/check_constant_frames.py` enforces this. It reads the suffixes out of the
rebase's own source, so a family the walk grows and this paragraph does not name fails
the suite, and it rebases one probe constant of each shape as well — the dict moves,
the int beside it does not, and the entries the walk cannot shift come back unchanged
from a round trip.

## Releasing

Every change a user runs bumps `version.txt` and gains a `## <version>` section in
`CHANGELOG.md`. Not a convention — the bot reads both from GitHub to tell people an
update is waiting, so a forgotten bump is a fix nobody is told about.

**Prose is not a release.** No `.md` file is, anywhere, along with `devtools/`,
`readmes/`, `references/` and `.github/`. Nobody pulls a new build to get a paragraph,
and a version that moves for a credits rewording teaches people to ignore the banner —
which is exactly how the old commit hook's per-commit increment made the number
worthless. Documentation rides along with the next change that does something.

`devtools/check_version_bump.py` enforces this against `origin/main`.

**Only the third digit moves without asking.** Every release bumps it — a fix and a new
capability alike, for now. The first two digits change only when the owner has said so
in the conversation, for that release: propose it and wait, never decide it. Whether a
change "is a new capability" is exactly the call an agent answers generously, and a
number people read as "how much changed" is only worth anything if its big steps are
chosen. `check_version_bump.py` fails a first- or second-digit move unless it is run
with `ALLOW_VERSION_JUMP=1`, which is the step that records the permission was given —
setting it without that permission defeats the point.

`npm run build` syncs the number into `web/package.json`; nothing else needs touching.

## Verifying a change

`--source adb` is the verification:

```
.venv\Scripts\python.exe devtools/replay_independent_screens.py --source adb
```

**Do not run the desktop replay unless asked for it.** The `check_*.py` suites in
`devtools/` are offline and safe to run freely. Mutation testing is the standard
here: after writing a test, break the code it covers and confirm the test fails.
Several tests in this repo passed against the bug they were written for.

## Style

Two-space indentation in Python, repo-wide. Not PEP 8, but it is consistent — match
it rather than reformatting.

`SCREEN_ORDER` is specific-before-generic; that ordering is what resolves ambiguous
screens, so a new spec goes in a considered position, not on the end.

## Where the docs are

- `README.md` — for people running the bot
- `BACKLOG.md` — the plan, the phase history, and known-broken things. Large; read it
  when the work touches its subject, not by default.
- `readmes/` — FAQ and guides.
