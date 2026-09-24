# Backlog

Open work on the Independent Training loop, and the reasoning behind what has been
deferred or ruled out. Kept in the repo so it is versioned with the code it describes.

Two things this file is for. The first is the obvious one: what is left to do. The second
matters more — several entries hold analysis that was expensive to produce and is easy to
redo by accident. Where an entry says **do not re-derive**, the work has been done and
writing it down was the point.

Entries carry the date they were written. Anything describing the game's screens is only
as current as its date; the game changes.

---

## Read first: the scheduler and multi-instance redesign

*2026-08-30. Rescoped 2026-09-02 into one project, then ordered scheduler-first the same
day. They stay one project because they share a control plane and a state key; they ship
in the order below because the scheduler is what is actually wanted first.*

A structurally different design for the main loop — an infinite to-do list of tasks rather
than the career-centric loop it is today — eventually running against several Android
emulators at once. Under that design "wait for TP" is not a feature at all; it is a longer
wait on a task.

**Nothing that adds a new *kind* of work to the loop should be built standalone until the
scheduler lands**, because it would be built twice. That covers at least: waiting for TP,
daily races, and Legend Races. It does not pin bug fixes, or anything that changes how
existing work behaves — "Ready to build" below is all unpinned.

**The two are coupled at exactly two points**, and both are cheap to get right in advance:
the key the scheduler writes its state under, and the shape of the control surface that
starts and stops a bot. Get those two right in the scheduler phases and multi-instance is
additive afterwards rather than a migration. Everything else about multi-instance —
config scoping, the stats files, the resolution and duplicate-device guards — is
independent of the queue and can wait.

### Already established — do not re-derive

Modelled on ALAS (`LmeSzinc/AzurLaneAutoScript`), whose Overview shows Running / Pending /
Waiting against a task list. The full write-up, with code excerpts and phase sizing, is at
<https://claude.ai/code/artifact/ac0d8682-52b7-4a42-a033-eeb9a7ec8996>.

**The scheduler already exists.** It is the tail of `handle_home`: `_run_team_trials_if_due`
then the career fallthrough. Enable flag, cooldown timestamp, resource-gated due-check,
priority order, default task. The 66-screen state machine does not move — this replaces one
function's tail with a dispatcher and moves `RunState` fields into a file. Team Trials was
built twice without anyone noticing, and every field the design needs already exists under
another name:

| Today | Under the queue |
| --- | --- |
| `state.tt_idle_until` + `tt_finished` | `task.next_run` |
| `_tt_stand_down(state, seconds)` | `scheduler.defer(task, seconds)` |
| `_run_team_trials_if_due` | `task.check()` + `task.enter()` |
| `TT_RECHECK_SECONDS`, `TT_TALLYING_RECHECK_SECONDS` | success / blocked intervals |
| `handle_career_complete` | `scheduler.done(career)` |
| `bot.stop_after_career` | stop when the queue next idles |

**A task's due-check returns `Ready` or `Retry(seconds, reason)`, not a bool.** ALAS gates on
the clock alone; here TP and RP are read off the screen, so a refusal has to say when to look
again. That one choice is what makes waiting for TP fall out for free:
`Retry(seconds=(cost - current) * 600)` replaces the `_stop` at the `blocked = "TP refill is
switched off"` branch, and Team Trials fills the gap by being the next due task. No new
screens, no captures. It is also what pays for the host lease in Phase 4, unchanged.

**The idle wait is a blocking handler, not a poll.** `MAX_ACTIONS_PER_RUN = 400` is not the
obstacle an earlier entry claimed: `handle_training_in_progress` already blocks inside the
handler for ~50 minutes a career and is charged one action, not thousands. Build the wait in
that shape and no budget exemption is needed. What does carry over: `time.sleep` not
`utils.tools.sleep` (`SLEEP_TIME_MULTIPLIER` would halve a five-hour hold at 0.5), sleep in
chunks so a stop still lands promptly, and re-identify periodically — that handler returns
the moment the countdown stops reading, which is how it notices the screen changed underneath
it.

**Daily tasks need no timezone arithmetic.** `Screen.DATE_CHANGED` has a handler and a capture
and fires exactly on the server rollover; mark daily tasks due from `handle_date_changed`.
Keep a coarse clock estimate only for a session that was not running when it rolled.

**`next_run` goes in `stats/`, not `config.json`** — runtime state, not settings. The config is
duplicated across three files and the preset system copies configs between machines; the queue
writes at every task boundary.

**One process per emulator is forced, not chosen.** Two module-level singletons each
independently rule out two devices in one process: `utils/adb_actions.py` holds a single
`device` handle and a single `cached_screenshot`, so a second device's connect re-points
the handle every caller shares and either device can be served the frame the other one
captured; `core/config.py` loads settings into module globals, so one `Device ID` and one
`USE_ADB` describe the whole process.

`utils/constants.py` holds one coordinate frame per process as well, but it is a frame for
the **platform**, not for the device, and it is not what rules two devices out. The ADB
path asks for the same `offset=-155` for every device that takes it (`main.py:83`, mirrored
in `auto_misc.py:53`), and `bot.use_adb` is one process-level value (`core/bot.py:17`), so
two ADB devices in one process name the same frame and the second call is a same-offset
no-op — the case `devtools/check_constant_frames.py` pins in A3. An earlier version of this
entry read it the other way instead: as a second device re-framing the constants out from
under the first with "the two frames 560px apart", and as the old one-shot guard leaving
the first device right and the second one wrong. 560 is `405 - (-155)`: the distance
between the ADB frame and the desktop default (`main.py:108`), the two frames one process
reaches over two *runs* of the bot, not a distance between two devices. And the dropped
second `-155` call under the old guard left the constants in the frame both devices had
asked for, so neither half of that reading holds. The rebase's own contract is unaffected
and is right for the frames a process really reaches: it mutates module globals, remembers
the offset currently baked into them (`_APPLIED_OFFSET`) and shifts by the difference when
a call names another frame.

**Half of the multi-instance work is already built.** The port scan (8000–8010), the
per-instance hotkey, `logs/{hotkey}/images/` and `--use-adb` overriding `device_id` are all
instance-aware today — `start.bat --use-adb 127.0.0.1:5565` already starts a second instance
against a second emulator.

### The trap, and the one line that defuses it

`main.py:287`, inside the port scan:

    bot.instance = port - start_port + 1
    bot.hotkey = f"f{bot.instance}"

An instance is **whichever port happened to be free when it started**. Nothing declares it.

Today the cost is only confusion: logs land in `logs/f2/`, and `utils/webhook.py` already
stamps `(Instance N)` on every Discord message, so which emulator a stop notification refers
to depends on start order. Start the second one while the first is down and it *becomes* the
first.

Under the queue that would stop being cosmetic. Key the state file on that number and a
restart in the wrong order makes a process **resume another emulator's task queue** — its
deferrals, its cooldowns, its career count — against a different account, with nothing
reporting an error.

**The fix is not to build multi-instance first. It is to key the state file on `device_id`.**
That string is already stable, already declared, and already the real identity of the thing
the queue is scheduling: `bot.device_id = config.DEVICE_ID or "127.0.0.1:5555"`, overridable
by `--use-adb`. `stats/127.0.0.1_5555/schedule.json` says what it belongs to on its face,
where `stats/1/schedule.json` does not. The desktop path takes the literal `desktop`.

That is the whole coupling. With it, the scheduler can ship years before multi-instance and
nothing migrates. Two consequences worth writing down: changing an emulator's ADB port resets
that queue, which is harmless — every task simply becomes due; and two processes pointed at
one `device_id` would now *share* a state file rather than swapping them, which is racy but a
better failure than the swap, and is closed properly by the duplicate-device guard in Phase 5.

### The web UI becomes the control surface

*Decided 2026-09-02: an ALAS-style UI, in the web UI that already exists.* Today the only way
to start or stop is a global hotkey, and that does not survive what is coming — hotkeys fire
regardless of which window is focused, `f1`…`f10` runs out, and the instance they name is the
positional one above.

**This is a small change, not a startup inversion.** `main.py:262-269` already toggles
`bot.is_bot_running` and spawns `threading.Thread(target=main)`, and uvicorn already runs in
that same process. `POST /bot/start` and `POST /bot/stop` call the same two things the hotkey
handler calls. There are no such endpoints today — the server has config, theme, stats, ADB
test and webhook test, and nothing that touches the bot's lifecycle.

What the UI owes, in rough order of value: start / stop / stop-after-career; the queue itself
as Running / Pending / Waiting with each task's `next_run` and the reason for its last
deferral; and run-now, which sets `next_run` to zero and is the whole reason a queue is nicer
to operate than a loop.

**Keep the hotkeys.** They are the emergency stop when the browser is not focused, and
removing them would be a regression mid-career. They stop being the *only* way in, which is
the point.

**Write it against a list of instances that happens to have one element.** Not a special case
for one, and not a rewrite later — the same principle as keying the state file on `device_id`.
The Overview renders rows from a list; in Phase 3 that list has one entry, and in Phase 5 it
has several.

### When multi-instance does arrive

**Config: a self-contained file per instance, and one small machine file.** *Settled
2026-09-09, against the overlay this section proposed first.* Each instance owns
`config/instances/<name>.json` — a whole config, the shape the preset files under `config/`
already are — and the few genuinely machine-wide keys move to `config/machine.json`.

| Per instance — `instances/<name>.json` | Machine-wide — `machine.json` |
| --- | --- |
| `device_id`, port, hotkey | OCR engine, `OCR_USE_GPU` |
| trainee and aptitudes, borrow cards, priority skills | `SLEEP_TIME_MULTIPLIER` |
| training focus, racing style | notification sounds and volume |
| TP refill strategy and switches | debug flags, debug-image saving |
| Team Trials enable and keep-charges | the webhook URL |
| `INDEPENDENT_MAX_RUNS` | |

The overlay — one shared base plus `instances/<name>.json` carrying only the keys that differ
— was the original plan and is the worse fit, for a reason this section's own table gives:
almost everything in it is per-instance. The overlay would be ~90% of the file, and the keys
it did *not* carry would silently follow the base, so editing one instance's borrow card
would change the other's with nothing saying so.

Splitting by ownership also dissolves the problem the overlay left open, rather than solving
it. A machine-wide setting edited in one instance's UI must not apply only there — under the
overlay that needs either a write-through to the shared file or read-only fields outside one
designated instance. With one file that owns those keys there is nothing to arbitrate.

**The drift objection is real and costs about five lines.** N copies of a config do go stale
against a new key, and `reload_config()` reads with bare subscripts, so a missing one raises
`RuntimeError` naming it (`core/config.py:194`). The answer is to merge each instance file
over `config.template.json` at load, so an absent key takes the template's default instead of
raising. That is worth doing on its own account: the preset files today can go stale exactly
the same way.

**Two things this section had wrong about the code, checked 2026-09-09.** `load_config()`
hardcodes `"config.json"` and takes no path (`core/config.py:13`), so per-instance loading is
a real change there rather than a caller passing an argument. The `update_config()` that does
take a `file_path` is `update_config.py:25`, a migration tool, not the loader — and not the
`update_config()` in `server/main.py:208` either.

*Settled 2026-09-09: one webhook URL for the machine, in `machine.json`, with each instance
stamping its declared name.* `utils/webhook.py` already stamps an instance marker on every
message, so this mostly falls out of declared identity; what changes is that the marker stops
being positional.

**Cross-instance Overview without a supervisor.** ALAS owns N subprocesses behind one UI. The
cheaper shape here: keep N servers as today, and let each one *read* every instance's state
file — it is JSON on one disk — while control actions stay local to the instance that owns
them. It degrades correctly, since an instance that is not running just has a stale state file,
which is exactly what an Overview should show.

**Host contention is the only thing to arbitrate, and `Retry` already carries it.** Each
emulator is an independent account, so there is nothing to arbitrate between instances except
the host. Jitter on every `defer` only lowers the odds of a collision. The real answer is cheap
once the queue exists: the scheduler is the only thing that knows a task is *about to* spend
minutes in OCR — the skill survey, the stat reads — so give those tasks a host-wide lease, a
lock file under `stats/` with an owner and an expiry, and a task that cannot take it returns
`Retry(seconds, "another instance is surveying skills")`. Same return value as TP, no new
mechanism. Do not add jitter before this; with one instance it does nothing.

**What has to be measured before choosing how many to run**, none of it guessable, and the
first item is worth doing early because it also prices the PaddleOCR/Tesseract switch:
resident memory of one instance **during a skill survey** (each process loads its own torch and
EasyOCR); the same with two actually running, not one doubled, since contention is not linear;
and whether the shared adb server serialises concurrent screencaps, which
`devtools/adb_probe.py` can drive.

**Measured 2026-09-09 by `devtools/measure_footprint.py`. Memory is not the constraint,
and the premise above is out of date.** There is no torch and no EasyOCR any more -- the OCR
is RapidOCR on onnxruntime -- so the figure this paragraph was written to find does not
exist. One instance, imports and both OCR engines and the web server included, peaks at
**263 MB**:

| | 1 instance | 2 | 3 |
| --- | --- | --- | --- |
| peak working set | 263 MB | 529 MB | 791 MB |
| `identify_screen` | 1432 ms | 1695 ms | 1977 ms |
| skill survey, 22 rows | 1.0 s | 2.4 s | 5.6 s (at 4) |

*Machine: 16 logical cores, 20258 MB.*

**The emulator is the cost, not the bot.** `MuMuVMMHeadless` is 1575 MB, six times an
instance, so what has to fit is emulators plus ~260 MB of bookkeeping each. On this box, as
it is normally loaded, a second emulator fits and a third does not -- and that has nothing to
do with any code in Phase 5.

**The contention is the opposite way round from the assumption.** This paragraph sent the
measurement after the skill survey, and the survey is the wrong thing to watch: it is a
couple of seconds once a career, and it degrades roughly linearly because the OCR already
saturates every core. What runs constantly is `identify_screen`, once per pass of the loop,
and at 1432 ms it costs more per frame than the whole survey costs per career -- but it
degrades *sub*-linearly, 18% slower at two instances and 38% at three. So the steady state is
comfortable at two or three, and the burst is what collides. That is an argument for the
Phase 6 lease, and a narrower one than the entry above makes: it needs to cover the survey,
not the loop.

**Everything else that breaks at N > 1**, none of it caused by the queue:

| Thing | Why it breaks | Size |
| --- | --- | --- |
| `stats/pending.json` | read-modify-write; two instances corrupt it | scope per instance |
| `stats/runs.jsonl` | interleaves careers from different accounts | scope per instance, aggregate on read |
| ~~`logs/incidents/`~~ | **was already scoped** -- it takes `log_dir`, which `init_logging` sets per instance. The line this cited reads that variable rather than hardcoding a path | none |
| ~~Discord `(Instance N)`~~ | worse than positional: `_STOP_STYLES` is module-level f-strings evaluated at import, so **every** instance said `(Instance 1)` for the life of the process | done in 5b |
| Two instances, one `device_id` | they kick each other through the session-verification path, which now stops the bot | refuse at startup from the instance registry |
| An emulator not at 800x1080 | already warns once (`utils/adb_actions.py:97`) but does not stop, and one warning among N logs is missed | make it a refusal for that instance |
| Fixed-attempt settle loops, `min_search_time` | tuned on an idle host | audit under contention — probably fine, since they wait for stability rather than a deadline, but unverified |

### Restarting the game as a recovery

*Asked 2026-09-02.* The premise holds: the walk from a cold start back to a running career
already exists and is exercised every day. `TITLE_SCREEN` has a handler, the login
interstitials have a chain with a 40-screen budget, `DATE_CHANGED` catches the rollover, and
`handle_home_career_in_progress` resumes a career whose progress is held server-side. That is
what makes this cheap — the recovery path is already built and already tested. The only new
mechanism is `am force-stop` + `am start`, and `utils/adb_actions.py` has no generic `shell()`
yet: it has `click`, `swipe`, `screenshot` and nothing else. ADB-only; the Steam path is not
worth the trouble and is not the priority.

**It must not be a blanket response to stopping.** The ~23 `_stop` sites split three ways.

Restart plausibly helps — the game is in a state the bot cannot navigate out of, and anything
it cares about is server-side:

- "Could not recognise the screen for too long" (`unknown_frames`)
- "The game has been connecting for N frames"
- "Cleared 40 first-login screens without reaching home"
- "Could not leave the Career Complete dialog" and "…the Training Log's Career page" — both of
  which already say the results are recorded
- "Career exceeded 400 actions without completing"
- "The training countdown stopped advancing", "Training did not finish within N minutes"
- "Start did not take after N presses"

Restart is useless, or actively harmful:

- **the session-verification pair** — "signed in from somewhere else", and "logging back in
  here would sign that device out and start the two swapping". Restarting is *precisely* the
  kick war those stops exist to refuse. This has to be excluded by name, not by omission.
- "Failed to connect to ADB device" — `am start` needs the device the stop says is unreachable
- "The game refused a career for want of TP N times" — a resource problem, not a state one
- `CLAW_MACHINE` — needs a person
- "no card artwork matched" — configuration, and a restart loops on it forever

And every `FINISHED` stop is a legitimate end: out of TP, the run cap, stop-after-career. None
of them restarts.

**The loop guard is the feature, not a detail.** A template regression — a render change on a
new client, which this install produces routinely — presents exactly like the first group: the
screen stops being recognised. No number of restarts fixes one. Without a guard this trades a
loud, diagnosable stop for a silent loop that burns a day and reports nothing, which is
strictly worse than stopping. The minimum is a per-session budget, backoff between attempts,
and **stop for real when a restart lands on the same stop reason twice**. That last part is
what separates this from a retry loop.

**Keep the evidence.** `save_incident_image` fires on STUCK today, and it matters more after
this rather than less: nobody is watching, and the screenshot is the only thing that will say
why a night had four restarts in it. Capture before restarting, and announce the restart the
way `on_recovering` already announces a connection drop.

**Where it goes: after Phase 1.** The backoff is `Retry`, and "get back to home, then work out
what to do next" is the queue's whole job. The budget can sit on `RunState` — restarting the
game does not restart the bot, so nothing here needs to persist.

**~~Open: the package name.~~** Not in the repo, and not worth guessing between the JP and
global builds. Read it off the device instead — the foreground activity from `dumpsys
window` — print it from `adb_probe`, and keep it as a config key defaulting to whatever
that finds.

**Settled 2026-09-06, and the advice above is half a trap.** Reading it off the device is
right; reading it *at the moment of the restart* is wrong by construction, because a
restart is wanted precisely when the game is not up, so the foreground then is by
definition something else. It cost a night: the game segfaulted on relaunch and left the
emulator on its launcher, and the next stuck stop read `app.lawnchair` and force-stopped
and relaunched the user's own launcher. The reading has to happen on a *matched* frame —
every screen in `SCREEN_ORDER` is a screen of the game, so if one matched then the
foreground is the game — and be remembered from there. Never seen running now means
nothing to restart, and it says so rather than restarting whatever took its place.

### Phases

**0. Port the two existing tasks** — career and Team Trials — to a dispatcher, with no
behaviour change. State at `stats/<device_id>/schedule.json` **from the first commit**. The
test is that the replays and one live career are unchanged. *Built on `feature/task-scheduler`
(a2e32b3): `core/scheduler.py`, `devtools/check_scheduler.py`, every offline suite passing and
the check mutation-verified. **Confirmed working against live careers, 2026-09-04.** That
closes the half of the acceptance test the offline suites could not reach; Phase 0 is done.*

**1. `Retry` and the idle wait.** TP deferral and the long hold. The payoff phase, and the
point at which "wait for TP" exists without having been built as a feature. *Built and
**verified live on 2026-09-02**: the career deferred, Team Trials filled the wait, the hold
ended and the career started -- `TP 73/100` then `scenario_select` in the log. The
screen-change exit fired too (`The game moved to tt_race_menu while waiting`), and the
restart recovery got an unplanned live run out of the same session.*

**1b. Restarting the game as a recovery** — classified by stop reason, budgeted, and with the
same-reason-twice guard. Sits here because the backoff is `Retry` and the walk back to home is
the queue's job. See above for what must never restart.

**1c. The two TP stops that are still stops.** `handle_tp_too_low` and
`handle_recover_tp_list` detect the same "out of TP, cannot refill" condition as the home
screen, but from a dialog rather than from home -- and both dismiss onto the *setup*
screen, not home, so deferring there needs a way back that does not exist yet. They are
the disagreement path (the home reading was wrong) rather than the normal one, so they are
left stopping for now.

**2. UI control.** `POST /bot/start`, `/bot/stop`, `/bot/stop-after-career`, `/task/{name}/run-now`.
Hotkeys stay as the emergency stop. *Built on `feature/task-scheduler`: the endpoints, a
Start/Stop control in the header, and `Scheduler.reload_if_changed` so a run-now written by
the request thread is picked up by the bot's own scheduler. Checked by
`devtools/check_bot_control.py` and smoke-tested over real HTTP.*

**3. The Overview** — Running / Due / Waiting, `next_run`, last deferral reason — rendered
from a list of instances with one entry in it. *Built on `feature/task-scheduler`: an
Overview tab reading `/bot/status`, with Run now on any waiting task. The queue is assembled
from two sources because neither is complete alone — the task order and the full set of
names from the bot, the cooldowns from the file, so a task that has never waited for
anything still appears and a stopped bot still shows why it is not racing.*

**4. New tasks: missions and the present box.** *Built on `feature/task-scheduler`: two
screens (`MISSIONS`, `PRESENT_BOX`), two handlers, two tasks between Team Trials and the
career, and `handle_date_changed` clearing both so the server rollover is the daily signal
rather than any clock arithmetic. Both default on; either can be switched off alone.*

*Daily races and Legend Races are **dropped from this phase** (asked 2026-09-03). They are
the two expensive ones -- new screens, new decisions, and in the Legend Races case a whole
mode -- and neither is a daily chore in the way these two are.*

*Daily races then shipped anyway, later the same day, as their own task rather than as
part of this phase -- see "Daily races" under Ready to build. Legend Races are still
untouched and still out of scope.*

**5. Multi-instance.** Broken out 2026-09-09. The state files are already keyed correctly
(`core/scheduler.py:135`), which is the one part that would have forced a migration, so what
is left is identity, config and scoping. Ordered so that each step is useful alone and
nothing before **5d** changes behaviour for a single instance.

- **5a. Measure first.** *Done 2026-09-09, `devtools/measure_footprint.py` -- see the
  table above. It did not invalidate the rest: an instance is 263 MB and the emulator it
  drives is 1575 MB, so memory decides how many emulators the box holds and says nothing
  about the bot. Re-run it if the OCR is replaced, which is the one change that could
  move these numbers.*
- **5b. Declared identity.** *Done 2026-09-09.* `--instance <name>` names the process;
  `--port` and `--hotkey` pin what the scan used to assign. `bot.instance_label()` and
  `bot.instance_dir()` are what everything derives from, and the three copies of
  `if bot.hotkey == "f1"` in `utils/log.py` are now one call each. Undeclared, an
  instance behaves exactly as before -- the first still owns `logs/` itself.
  `devtools/check_instance_identity.py`, mutation-verified 9/9.

  Two things turned up that were worse than this phase assumed. The Discord marker was
  not positional but **constant**: `_STOP_STYLES` builds its titles at import, before
  anything has an identity, so every instance's stop notification claimed to be
  instance 1 -- for the life of the process, whichever emulator it was driving. And the
  port scan had no exhausted-range branch: with 8000-8009 all taken it fell through
  with `port` left on the last candidate and identity still at its defaults, so an
  eleventh instance bound a port in use *and* wrote over the first one's logs. Both
  fixed here; the first is why the marker is now applied at send time.
- **5c. Config.** *Done 2026-09-09.* `config_path()` answers which file this process
  owns -- `config.json` undeclared, `config/instances/<name>.json` otherwise -- and is
  resolved per call rather than captured at import, which is the mistake the Discord
  marker made. `load_config()` takes a path and layers three: the template underneath,
  the file, then `config/machine.json` for the keys that belong to the box. A key the
  file omits now takes a default rather than raising, and `update_config()` already
  took a path, so an instance file is created from the template and kept current by
  the code that has always done that for `config.json`.

  Nothing moved for a single instance, and that is checked rather than asserted: with
  no `--instance` the layered load returns exactly what reading `config.json` returns,
  key for key. It can, because a config complete enough not to raise today has nothing
  for the template to fill.

  A machine-wide key still sitting in an instance file is reported at startup and
  overruled, not silently absorbed -- dead text that looks live is how an afternoon
  goes into editing a value nothing reads. `devtools/check_instance_config.py`,
  mutation-verified 11/11. `check_bot_control` had to move with it: it redirected the
  server by setting `server.CONFIG_PATH`, and that seam is now `config_path()`. It also
  stopped needing to replace `load_config` wholesale, which it only ever did because
  the loader hardcoded its file.
- **5d. Scoping the writers.** *Done 2026-09-09.* `runs.jsonl` and `pending.json` sit
  under the device key beside `schedule.json`. Their paths were function defaults, so
  they were bound at import, before the device is read from the config or `--use-adb`;
  they are resolved per call now, like `config_path()` and for the same reason.

  **Nothing is migrated, deliberately.** Moving the old history would have to decide
  which device it belonged to and the file cannot say -- the only honest answer is
  "whichever emulator was running". So the pre-split file is read forever and written
  never, and the 221 careers already on this machine survive untouched. Records carry
  `device` from here on, which is what a per-instance view would filter on later; the
  ones without it are the ones from before there was a question.

  The pending tally is *not* carried over, which is a smaller decision than it sounds:
  it holds refill timestamps with a two-hour ceiling, so the whole cost is one career
  undercounting refills once, against a migration needing the same unanswerable guess.

  `/stats/runs` aggregates across devices and `/stats/reset` clears what it showed --
  one person, however many emulators. `devtools/check_stats_scoping.py`, mutation-
  verified 11/11, and its docstring carries a warning worth heeding: the mutations that
  break path isolation make the cases write fixtures into the real `stats/`.
- **5e. Refusals.** *Done 2026-09-09.* A device is claimed by an open file handle, so
  a second bot on one emulator is refused by name and pid. Held rather than recorded
  because a pid file needs a liveness check, and on Windows the obvious one is a trap:
  `os.kill(pid, 0)` does not probe a process there, it calls TerminateProcess. A held
  lock needs no check -- the OS drops it however the process ends, so a crash gives the
  device back. A device that is not 800x1080 is refused on connecting, once, while the
  per-frame path keeps warning: one odd frame is a glitch, and ending a career over it
  would be worse than the misalignment. `devtools/check_instance_refusals.py`,
  mutation-verified 11/11.

  Found on the way: `main.py` cleared `is_bot_running` only on the path that actually
  ran, so a failure to start left the flag set -- the next hotkey press stopped a
  corpse and the one after it started anything. Every refusal added here goes through
  that same place, so the check walks main's AST and requires every early return to
  clear it, rather than counting the string.

- **5f. The Overview gains rows.** *Done 2026-09-09.* `/bot/status` builds one row per
  emulator: this one live, the others read off `schedule.json` and `owner.json`. No
  supervisor and no talking between processes -- an Overview is a directory listing and
  two reads, and an instance that is not running just has a stale file, which is what
  an Overview should show. The UI needed almost nothing: it was already written against
  the list rather than its first element, as this section asked for in August.

  **`local` is the field that matters.** Run-now and clear-hold act on *this* process's
  scheduler, so on another instance's row they would drive the wrong emulator while
  appearing to drive that one. The buttons are gated on it, and the check asserts the
  gate in the file that renders them. A remote row is built from cooldowns alone: task
  order, which tasks are off, and which one is running all live in this process's
  memory, and claiming any of it for another instance would be inventing it.

  Liveness comes from the claim's pid through a read-only `OpenProcess`, never by
  probing the lock -- a status endpoint a browser polls must not be able to refuse a
  bot that is starting at that instant.

  Found on the way, and older than this phase: the server resolved which device it was
  only when the bot started, so until then every state file it read was `desktop`'s. The
  Overview showed an empty queue for itself and listed the real emulator as somebody
  else's instance. `resolve_device()` now runs at startup as well.


**6. ~~The host OCR lease~~ -- measured 2026-09-09, and not built.** The premise is the
same one 5a retired. This phase was written when a recognize call cost ~0.5s under the
CPU torch build, so a skill survey was "about to spend minutes in OCR" and worth
serialising. Under RapidOCR a row costs ~45ms and a whole survey is about two seconds,
once per fifty-minute career. There is nothing there to arbitrate.

Worse, a lease is the wrong shape for the contention that does exist. Serialising helps
only when sharing is *super*-linear -- thrashing -- and 5a measured the dominant load,
`identify_screen`, degrading **sub**-linearly: 18% slower at two instances, 38% at
three. A lease would turn an 18% slowdown into a 100% wait for whoever queued. The
reason it degrades so gently is worth recording: capping cv2's thread pool changes
nothing measurable (1285ms at 16 threads, 1248ms at 4), so the matching is effectively
single-threaded, and two single-threaded workers on sixteen cores barely meet.

**What is real, and still not worth taking yet.** The OCR *is* oversubscribed --
onnxruntime defaults to every core, so N instances put N*16 threads on 16 cores.
Capping `intra_op_num_threads` to 4 measures as:

| | uncapped | capped to 4 |
| --- | --- | --- |
| one instance | 0.86s | 0.93s |
| two instances | 2.09s | 1.22s |

*Survey of 22 rows, 16 cores. Best of two.* So it costs ~8% alone and saves ~42% at
two -- but 42% of a two-second survey is under a second per career, against a config
key that would need the five places in CLAUDE.md. Left unbuilt deliberately, with the
numbers here so it is a one-line change if the box shrinks or N grows. `2` and `3`
measure within noise of `4`; `8` and above are worse than uncapped at two instances.

**The lever this phase was looking for is somewhere else**, and it has its own entry
under "Wanted, not yet analysed": `identify_screen` costs ~34-50ms per spec tried and
stops at the first match, so what a frame costs is where its answer sits in
`SCREEN_ORDER`. `py devtools/measure_footprint.py --profile` prints it.


### Open, and blocking nothing before Phase 1

*Settled 2026-09-02: waiting for TP is a **setting**, `independent_training.wait_for_tp`,
defaulting to on. A setting rather than plain behaviour because it is a large change for
anyone who has been using "out of TP" as the natural end of a run; defaulting to on because
TP is never wasted by arriving while the bot idles.*

*Settled 2026-09-02: `INDEPENDENT_MAX_RUNS` caps the **session** — N careers since this
process started, then the bot stops. That is what it has always done, and the counter stays
out of `schedule.json` so the queue cannot quietly turn it into "N careers ever".*


- what "daily races" means;

---

## Platform priority: the emulator comes first

*Decided 2026-09-02.* ADB against the emulator is the target the bot is actually run on.
The Steam client stays supported where that is free, but where the two disagree the
emulator wins — do not hold up an ADB fix waiting for a desktop capture to match.

In practice this costs almost nothing, because anchors are ANY-of: a screen keeps its
desktop crop and gains an emulator one, and both clients end up with something that
scores 1.000. That is the shape every fix below took. What changes is the tie-break —
if a template can only ever satisfy one client, cut it for the emulator.

Consequences: the "Desktop captures for three ADB-cropped templates" item is now low
priority by policy rather than merely unscheduled, and **the template census is the
highest-value item in Ready to build.**

### What the emulator does to text — do not re-derive

*Measured 2026-09-02 across five screens in one session.* Text anchors do not survive the
move to the emulator, and they fail in two distinct ways that need telling apart, because
only one of them is fixable by tolerance:

- **Antialiasing.** Same glyphs, same size, different edge rendering. Costs 0.07–0.17.
  `home_post_career` 0.830, `tt_quick_off` 0.856. No scale helps.
- **Size.** The emulator renders some text a few percent smaller. Costs far more:
  `tp_use_carats_body` 0.544 at scale 1.0 against 0.845 at 0.94;
  `tab_independent_inactive` 0.725 against 0.929 at 0.98. A 2% shrink cost 0.204.
- ~~**Colour.** The TP receipt's body line is drawn blue on the emulator and brown on the
  desktop.~~ **Withdrawn 2026-09-02: this was never real.** `debug_window` wrote every
  debug image with red and blue exchanged (an RGB capture handed straight to
  `cv2.imwrite`, which expects BGR), and the reference capture behind that claim had been
  copied out of `logs/images`. Brown text came out blue. Green is the middle channel and
  survives the swap, which is why nobody noticed for so long. Fixed at the source; the
  affected capture is corrected and its anchors re-cut. Re-measured against true colour,
  that receipt's body line still fails at 0.478, so the *anchor* was warranted — the
  explanation was not. Cost of the artefact: one Close-button variant asset that was never
  needed, since the real score is 0.944 rather than 0.848.

  The lesson worth keeping: **a reference capture must never come from `logs/images`.**
  Use `adb_probe save`, which writes through `cv2.cvtColor(..., RGB2BGR)`.

Two things follow. **Scale-tolerant matching is not the answer**: even at its best scale
`tp_use_carats_body` only reaches 0.845, still under threshold. Re-cutting is. And
**sprite art is unaffected** — buttons on the very same dialogs score 0.97+ — so a screen
that reads as stuck rather than misclicking is almost always a text anchor.

**Do not lower the global threshold. Measured 2026-09-02, do not re-derive.** It is the
obvious reaction to a new machine rendering differently, and it is wrong twice over.
Swept over the 38 registered ADB captures:

| threshold | correct | wrong | unknown |
| --- | --- | --- | --- |
| 0.90 | 38 | 0 | 0 |
| 0.88 | 38 | 0 | 0 |
| 0.86 | 36 | **2** | 0 |
| 0.82 | 35 | **3** | 0 |
| 0.75 | 33 | **5** | 0 |

`unknown` is zero at every level, so there is nothing for a lower bar to rescue — and at
0.86 the Final Confirmation tabs start taking each other over, which is a misroute rather
than a stop. It could not reach the real failures anyway: they scored 0.432, 0.544, 0.719,
0.725, 0.830 and 0.856, so catching the worst needs ~0.43. A colour change is not a
near-miss; the correlation genuinely collapses. The fix for a new machine is one capture
from it (`adb_probe save`) and an extra anchor, or a discriminator that is not text.
Per-spec thresholds already exist for real one-offs — `HOME_CAREER_IN_PROGRESS` uses 0.82.

Where a discriminator can be colour instead of text, prefer colour. Two measured cases:
the borrow card's limit-break pips (41–46 cyan pixels filled, 0 unfilled) and the Final
Confirmation tabs (0.90–0.93 green on the active side, 0.000 on the inactive, across five
captures on both clients). Neither has any of the failure modes above.

---

## Ready to build

### Instances managed from the web UI, not the command line

*Asked and researched 2026-09-17.* Phase 5 made a second emulator possible --
`start.bat --instance <name> --use-adb <device>` gives it its own config, port, hotkey, logs
and queue -- but only from a console, and the web UI did not know instances existed. The
want is for the UI to do all of it: instance tabs along the top, the preset bar back
(select, apply, import, export), and buttons that add, launch and stop instances. Expect
**4-5 emulators per PC at most**; the emulator's ~1.6 GB is the limit, not the bot's ~260 MB.

**Shape: a hub and workers.** Each emulator still needs its own process -- the module-level
singletons that forced Phase 5's one-process-per-emulator have not gone anywhere -- so the
UI manages processes rather than running bots itself. Weighed and rejected: one process
driving several emulators (every singleton in the codebase becomes per-instance state, and
the loops then fight over one GIL), and independent peers the UI merely switches between
(something outside the UI still has to start each one). The hub is the process `start.bat`
already starts, still running its own bot off `config.json`, so a single-emulator user sees
no change at all.

**Phases, each usable on its own:**

1. **Per-instance device settings, and a banner naming the instance.** The bug that makes
   this first: the Setup page reads and writes one shared `config/setup.json` for every
   instance, and each auto-save writes that shared `device_id` into the instance's own
   file -- so editing anything in instance B's UI pointed B at A's emulator, rescued only
   by `--use-adb` winning at runtime.
   *Done 2026-09-17, and it found a second, worse cause:* `update_config()` treated an
   instance file as a preset and stripped every setup key from it at each startup, so
   an instance loaded the template's device whatever its file said. Instance files are
   now whole configs like `config.json`; the Setup page reads and writes a named
   instance's device, window and preset in its own file and leaves `setup.json`'s alone;
   machine-wide keys that `machine.json` sets are shown and saved there. A banner names
   the instance (device, hotkey, port) and the hotkey hint stops saying F1 regardless.
   Undeclared with no `machine.json`: byte-for-byte the old behaviour.
   `devtools/check_instance_setup.py`, 14 mutations caught.
2. **The page talks to whichever instance is selected.** One base URL for every `fetch`,
   and tabs that switch it between workers' ports. Localhost cross-port calls already pass
   CORS and the cross-site write guard.
   *Done 2026-09-17, differently:* a tab **goes to that instance's own page** instead of
   re-pointing this page's requests. This page holds one instance's unsaved state; with a
   shared page, an auto-save landing mid-switch writes it into the other instance, and
   every piece of loaded state needs resetting on each switch. Navigation cannot mix them
   up, at the cost of a reload: the pending save is flushed first, and the section and
   light/dark ride along in the query (each port keeps its own storage), dropped from the
   address bar on arrival. `/instances/live` asks ports 8000-8009 for `/instance` in
   parallel rather than reading files, so tabs show what is running now; a config file
   with no process shows as a greyed "not running" tab. An instance pinned with `--port`
   outside that range is not found -- the hub in phase 3 assigns ports inside it.
   `devtools/check_instance_switch.py`, 15 mutations caught.
3. **The hub launches, stops and re-adopts workers.** `+` creates `config/instances/<name>.json`
   and starts `main.py --instance <name> --port <p>`; the port is stored in that file so it
   is stable. **Workers must outlive the hub** -- restarting the UI must not kill a
   50-minute career -- and a restarted hub adopts them from the files and the liveness the
   Overview already reads. No console: output goes to the worker's log. The launch route
   runs only `main.py` with a validated name, never arguments from the request. The
   Windows process handling is the least certain piece; spike it before building on it.
   *Done 2026-09-18.* The spike settled it on real processes: a worker started with
   `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB` (breakaway
   dropped only if Windows refuses it) and output to `logs/<name>/console.log` kept
   serving after its launcher was killed, and a fresh hub listed it again with nothing
   extra built -- discovery already asks the ports. A hidden console rather than
   `DETACHED_PROCESS`, so anything the worker starts gets no window of its own.
   The spike also caught a real bug: `--port` pinned inside 8000-8009 left the hotkey at
   F1, so a launched instance answered the first one's key and one press started both.
   A pinned port now takes that port's key. And two processes can no longer share a
   name: `main.py` takes a lock on `stats/.instances/<name>.lock` (released by the OS
   however the process ends) and refuses to start without it.
   In the UI: `+` creates an instance (name + ADB address; settings copied from the
   instance doing the asking) and starts it; a stopped tab has Start; a running named
   tab has a close button, confirmed first, which asks that instance to stop its bot and
   exit. The default instance is not closable from the UI -- it is the console
   `start.bat` opened, and usually the page asking. **Not done: the port is not stored
   in the instance file.** The launcher picks the first free port each time, which the
   tabs follow anyway; autostart-with-hub would want it stored and is left for later.
   `devtools/check_instance_launch.py`, 22 mutations caught.
4. **The preset bar returns, applying to the selected instance.** Everything behind
   `SHOW_PRESET_PICKER` is still wired; what changes is that Apply targets the tab, and
   each tab shows the preset it runs. Editing a preset still writes through only to the
   instance that has it applied and is being edited -- another instance on the same preset
   picks the change up when it re-applies.
   *Done 2026-09-18, with that last point reversed.* Leaving the other instances on the
   old settings meant their pages -- which show the preset -- displayed settings their
   bots were not running. A preset is now **linked**: saving it writes it into every
   instance whose `preset_id` is that preset (`config.json` included when it runs it),
   keeps each one's Setup keys (device, window) untouched, reloads the saving instance
   and asks each other running one to reload through `POST /config/reload`. The bar
   names the other instances on each preset, the open preset says "Also runs on ...",
   deleting a preset another instance runs says so, and each tab shows its preset.
   `devtools/check_instance_presets.py`, 19 mutations caught.
5. **An emulator list for new instances** (`adb devices` instead of typing a port), a log
   tail per instance, and a README section. After this, nothing needs the command line.
   *Done 2026-09-18, and it found the worst hole of the five phases.* "Look for emulators"
   (adb connect on the known MuMu/LDPlayer/BlueStacks/Nox/MEmu ports, on request only)
   turned up 127.0.0.1:16384 and :7555 beside the 5555 the bot uses -- **the same MuMu
   instance on three addresses**, identical boot id and android id. The dialog offered
   the two aliases as free, and the device claim, keyed on the address string, would
   have let a second bot onto the driven emulator. Now the list groups addresses by boot
   id and pools their users, and the bot claims the boot id itself once adb connects
   (`stats/.emulators/`), refusing a second bot whatever address it was given. Test
   probes an address without re-pointing the process (the Setup page's test does, which
   is why it refuses while a bot runs). **Logs** tails `log.txt` or a launched
   instance's `console.log`, following it while open. README: "More than one emulator".
   `devtools/check_instance_emulators.py`, 19 mutations caught.

   **Left open:** a launched instance's port is not stored, so there is no
   autostart-with-hub; and the running default instance has to be restarted onto this
   code before it shows up as a tab or answers `/instance`.

   *Reviewed 2026-09-18; five findings fixed.* (1) The device and emulator claims lived
   as long as the process, which outlives the bot, so a stopped instance held its
   emulator -- and `claim()` answered "already mine" for any device, so moving an
   instance to another emulator in Setup kept the old one locked and left the new one
   unclaimed. Both are now claimed per run, released whenever `main()` returns, and a
   device change re-claims. (2) An unreadable boot id waved the bot through; it is now
   read three times and refused if it never answers. (3) Two launches in the seconds
   before a worker binds took the same port -- ports are now promised in-process until
   the launch settles -- and a launch answered "success" once a process existed; it now
   waits up to 20s for the worker to answer, returning `running`, `starting`, or an
   error carrying the end of its console output. (4) `console.log` duplicated every log
   line, unrotated and buffered: workers now run unbuffered with the console handler
   silenced, and each launch keeps only its own file and the previous one. (5) Instance
   creation takes the name with an exclusive create, and a scan disconnects the aliases
   it connected. `devtools/check_instance_hardening.py`, 27 mutations caught.

### Progress has to mean less than a whole career

*Diagnosed 2026-09-08, from the night the bot stopped with eight restarts unspent.*

The game's countdown froze four times that day. The bot detected each one correctly — the
timer crop was byte-identical across six polls spanning 5m32s of wall clock, and the game was
still frozen on the same frame hours later, so this was the client hanging and not a misread.
It restarted twice, and both restarts **worked**: the first bought 2h15m of running, the
second walked all the way back through the title, the login, Team Trials and into the career,
and counted down 11 minutes before the game froze again.

The third was refused, because all three stops were `countdown_stalled` and the same-kind
guard only knows that the last restart "ended in the same place".

The guard is right to exist — it is what stops a template regression becoming an all-night
restart loop — but it cannot tell *restarted and hit the same wall on the next frame* from
*restarted, recovered completely, ran for two hours, and hit it again*. The only thing that
clears `_last_restart_kind` is `_note_progress()`, and that has exactly one call site
(`scenarios/independent_training.py:2848`), immediately after a career's results are recorded.
A career is ~50 minutes; the freeze recurred in 19. So the kind never cleared and the guard
never got to see that its own restarts were working.

Progress needs a finer definition than a banked career. Getting back to home and resuming is
unambiguous progress, and so is a countdown that ran for ten minutes. Either would have kept
the night alive.

**This gets worse at N > 1**, which is why it sits ahead of Phase 5 rather than beside it: two
emulators is roughly twice the freeze rate against the same one of you, and the failure is a
bot that stopped hours ago and said so only on Discord.

*2026-09-11: mitigated, not fixed.* The same-kind limit is a setting now --
`restart_max_same_kind`, default 5, "Restarts For The Same Problem" in the UI -- so a
freezing emulator gets several tries in a row before the guard gives up, where it used
to get one. That covers the night above: it would have restarted a third time. It does
not make a two-hour recovery count as progress, which is still the real fix, and a
template regression now costs up to five restarts before it stops instead of one.

### ~~Two-pass skill buying~~ — **done 2026-09-03**

Measured on the first real run, counting captures between the log's own markers:

| | before | after |
| --- | --- | --- |
| survey | 279 | 244 |
| purchase phase | 765 | **338** |
| total | 1044 | **590** |

About three minutes a career, and no `Could not buy` in the whole log. The purchase
phase went from ~2.7 traversals to one; the reposition to the top is gone, and reusing
the frame `_scroll` already settled on took the walk from three captures a step to one.

### Caching the survey's OCR — tried, measured at 0%, removed

*Do not rebuild this without reading both paragraphs.* The idea was to hash each name
crop and read each distinct skill once. It shipped, ran, and reported
`Skill OCR: 0/108 reads served from cache (0%), 108 distinct crops held` — every crop
unique, not one hit.

**The key cannot work as built.** A real scroll re-renders the row at a new subpixel
position, so the glyphs are rasterised differently and the trimmed ink differs
byte-for-byte even though it is the same text. The offline test that passed was invalid:
it shifted a crop *window* across a static image, which moves the same pixels, where a
scroll moves the *content*. A test that cannot fail the way the feature fails.

**And the premise was stale anyway.** It came from a docstring recording 852 rows read
for 46 skills — 95% repetition. The instrumented run reads **54 rows for 38 skills**, so
about 30%, and the whole survey spends 16s on OCR against 219s on screenshots. OCR is
**7% of the phase**. Even a perfect cache was never worth more than a few seconds.

If it is ever revisited, the measurement that decides it comes first: several *real*
consecutive Learn captures, saved during a survey, to see whether any key can separate
"same row re-rendered" from "a different skill". The earlier fingerprint numbers
(13.6-21.6 within a row against 22.9 between skills) came from the same invalid
static-image method and should not be trusted either.

**Where the time actually is: screenshots.** 371ms each, transfer-bound — raw
`screencap` is *slower* (453ms) because uncompressed is 3.4MB where PNG is not. The only
real lever left is capturing fewer frames or streaming them (scrcpy/minicap), and
`_wait_until_settled` taking two or more captures per scroll step is where they go.

--- | --- | --- |
| survey, one traversal down | 279 | 1.7 min |
| purchase phase | 765 | 4.7 min |
| **total** | **1044** | **6.5 min** |

**The purchase phase is 73% of it**, and it is about 2.7 traversals: the priority sweep
up, a full reposition back to the top, and the extras sweep down. The reposition reads
nothing — it exists only to put the next sweep at the end it walks away from, and
`_scroll_to_end` walks it a step at a time because the list is the only thing that knows
where its ends are.

So the survey is the small half. Anything aimed at OCR — the crop cache, a different
engine — is working on 27% of the problem.

**Two passes: survey down, buy on the way back up.** 558 captures against 1044, saving
about 3 minutes a career, and the reposition disappears because the survey already ends
at the bottom.

**Why the sweeps were split, and why one sweep can now be safe.** The code comment
records the incident: a single sweep in screen order "spent the budget on whatever it met
first regardless of rank", and one run lost its three highest-priority picks while extras
below them were bought. That is a real failure and the split fixed it — but it fixed it
by ordering, when the actual requirement is a *reserve*.

`select_purchases` already decides the whole set before any buying starts, so which
skills get bought is not in question. What can still go wrong is the plan not fitting:
the on-screen price drifts from the surveyed one, and a sweep that meets extras first
can spend what a priority skill needed.

So carry a reserve: the summed surveyed cost of planned priority skills not yet bought.
Buy a priority skill on sight; buy a planned extra only while `balance - cost >= reserve`.
That is order-independent, so one sweep in either direction is safe.

The drift is in the forgiving direction. A hint discount takes 30-40% *off* a surveyed
price, so the reserve over-estimates what the priorities will actually cost and the sweep
is conservative about extras rather than optimistic.

It also guards exactly the case that broke: extras are chosen from the bottom of the list
upwards, so a sweep walking up from the bottom meets them first.

**What has to be handled, none of it new work:**

- A priority skill that turns out unaffordable must release its share of the reserve, or
  the extras behind it are starved for the rest of the walk.
- Multi-press skills (`presses_for`) settle at a price only after the last press, so the
  reserve should come down once, not per press.
- The early exit is `len(bought) < len(wanted)`, which already stops the walk as soon as
  the plan is complete; with one sweep it applies to the whole plan rather than to each
  half of it.
- Dropping the pre-sweep `_scroll_to_end` depends on the survey reliably ending at the
  bottom. It does — that is its termination condition — but the sweep should still cope
  with starting anywhere, since a mid-career restart can land it mid-list.

**Not reachable: one pass.** Buying during the survey would need the plan before the list
is known, and `maximize_rating` solves a knapsack over every row. Two is the floor for
the current selection logic.

---


### ~~Daily races: the handlers and the task~~ — **done 2026-09-03**

*Captured and specced 2026-09-03, built the same day (62cc5aa), then fixed over three
more commits as it met the live game. The analysis below is kept because it is the
design record, not because anything in it is outstanding.*

Built: seven screens, eight handlers, a `daily_races` task with its own switch, program
and difficulty settings with the last-used-runner warning in the web UI, and an exit that
walks back out to home. Two things the captures could not have told anyone, both of which
cost a live run:

- **Multi-Race is turned on rather than assumed.** `multi_race_is_on()` reads the pill by
  colour (green >= 0.35), which is the colour test this entry guessed at below.
- **Leaving is its own problem** (d7ed356). Three separate faults: Back pressed at the
  *Missions* screen's coordinates, Home sitting behind modals that scored 0.387 / 0.701 /
  0.372, and the totals screen pressing an exit in the same pass as Close.

Also fixed here: `e6b31b8`, where the race menu is shared with Team Trials and the
dispatcher's `tt_` prefix attribution stole the task that had entered it -- the `KEEP`
sentinel in `_task_owning` exists for that.

The flow

    Race (bottom nav) -> race_menu -> "Daily Program" tile
      -> Daily Programs        two tiles: Daily Races, Daily Legend Races (skipped)
      -> Daily Races           Moonlight Sho (Monies) / Jupiter Cup (Support Points)
      -> difficulty list       VERY HARD / HARD / NORMAL / EASY, scrolls
      -> Race Details          modal, "Multi-Race: On", Cancel / Race!
      -> Runner Selection      pre-filled, Back / Confirm
      -> Multi-Race            -/+ count, "Race! Consumes N"
      -> result                "Race N", placing, rewards, Complete

## What the screens say that the design has to account for

- **Six tickets a day**, shown as `6/6` top right and on the Daily Races tile, with
  "Resets in 20h". Not one race a day -- how many to spend is a real setting.
- **Multi-Race runs them in one go.** The count modal defaults to the full stock and
  says "Consumes 6". So the whole day's racing is one entry, not six trips.
- **The two races differ in reward, not flavour.** Moonlight Sho pays Monies over
  Kyoto Turf 1600m (Mile); Jupiter Cup pays Support Points over Nakayama Turf 2000m
  (Medium). Picking is about what you need, and the distance differs -- which is what
  makes the "last-used runner" warning worth showing.
- **Runner Selection arrives pre-filled** with whoever ran last and their strategy;
  the bot would press Confirm and take that. It is the screen the warning is about.
- **The difficulty banner is colour-coded** -- magenta VERY HARD, red HARD, orange
  NORMAL, green EASY -- so telling them apart can be a scorer rather than four
  templates, the way the existing colour tests work.

**Two things a naive implementation gets wrong**, both reported by the user rather than
visible in the captures:

- **Multi-Race may be off.** The captures were taken with it already on. If it is off,
  Race! spends one ticket and the day's remaining five sit there. The handler has to
  read the pill and turn it on rather than assuming, which means a spec or a colour test
  for its two states -- neither is captured yet.
- **The reward badges bounce.** The Missions and Present Box marks on the main menu move
  every second, so anything matched against them needs a search region covering the
  travel rather than a tight box. This is what would have broken the "read the badge
  instead of trusting the daily cooldown" idea, which is still unbuilt.

Still wanted from a capture: a race result **without** the mission CLEAR! toast -- the
anchor is deliberately low so it should hold, but that is reasoning, not evidence.
*Multi-Race in its off state is no longer wanted: it is decided by a colour test rather
than a template, so there is nothing to capture.*

---


*Found 2026-09-01, while fixing the stat OCR and the refill tally. None of these adds a
new kind of work, so none is pinned by the redesign.*

- **`handle_tp_recovered` counts a refill per detection, not per purchase.** The loop
  re-runs a handler on every pass its screen is showing, and there is no repeat-screen
  guard — only `unknown_frames` and `MAX_ACTIONS_PER_RUN`. 34 detections against 34
  purchases across every log, so it has never fired, but a Close click that missed would
  count refills that were never bought. Same shape as the `tt_home_btn` loop described
  under Team Trials.

- **`devtools/replay_independent_skills.py` dies on a stock PowerShell console.**
  `UnicodeEncodeError` printing a skill's `☆` under cp1252, before any check runs. One
  line at the top of `main()`.

- **Docs left stale by the 2026-09-01 README rewrite (1cb4daa).** `screenshot.png` is
  no longer referenced. *`readmes/BOT_GUIDE.md` is dealt with -- deleted 2026-09-03,
  along with the Quick Setup heading that existed to link to it. `readmes/LOGIC.md`,
  which still documented the removed career scoring, deleted 2026-09-20.*

- **70 Dependabot alerts on the default branch** (44 high, 21 moderate, 5 low).
  Untriaged. Probably mostly unreachable given the pinned torch/easyocr stack, but nobody
  has checked which are actually in a code path this bot runs.

---

## Blocked on a capture

### Desktop captures for three ADB-cropped templates

*2026-09-01. Low priority as of 2026-09-02 — see Platform priority above.* The epithet
award window, the Daily Sale popup and the Proceed? tail were cropped from emulator
frames because those windows have only ever appeared on ADB. If the Steam client is ever
driven again, those three screens need desktop captures to verify their templates there;
until then those specs are single-platform by construction. That is now an accepted
state rather than a gap to close, and the list has since grown: the emulator-only crops
also cover the post-career home label, the Quick Mode pill, the carats dialog and its
receipt, and the inactive Independent Training tab.

### ~~Scenario selection~~ — **done 2026-09-16**

*Built 2026-09-16:* `independent_training.scenario` ("Career Scenario" in the UI, default
`default` = press Next on whatever is selected). The capture answered the three questions
below: a **looping carousel** of four pages (URA Finale, Unity Cup, Trackblazer, Our Grand
Concert) turned by side arrows, with page dots; **selected then confirmed with Next**; and
the logo is artwork under moving particles, but the **description panel is flat readable
text** naming the scenario (Trackblazer's says "Twinkle Star Climax", not its own name).
`handle_scenario_select` reads that panel, turns right until it matches, and stops -- never
falls back to Next -- after 8 pages. Verified live on all four from every direction needed,
Next stubbed. The flow after it is scenario-independent as far as Support Formation, checked
under URA Finale; the Final Confirmation screen under the other three was not reached (TP
was short), but it prints the scenario's name at the top, which is a second place to check
it if that is ever wanted. `devtools/check_scenario_select.py`, 19 mutations caught.

*Asked 2026-09-04. Third of the same family -- see Race agenda selection and Support-card
deck selection below; all three inherit rather than choose.*

`SCENARIO_SELECT` maps to `handle_next`, which is the whole handler:

    def handle_next(state):
      _click(f"{BUTTONS}/next_btn.png")

So the career runs whatever scenario was left selected last time. The want is to name one
in config and have the bot select it.

**Not already covered by the tab check, despite appearances.** `handle_final_confirm_normal_tab`
does assert Normal Career versus Independent Training and presses across when it lands on
the wrong one, so it is easy to read the setup flow as already pinned. That tab is the
career *type*. The scenario is a separate choice on a separate screen, and nothing asserts
it at all.

**Worth confirming first, because it decides how big this is:** whether the scenario
changes any screen the loop drives. An Independent Training career is largely a timed run
-- `training_in_progress` and `read_remaining_seconds` -- so the expectation is that the
scenario changes rewards and results rather than the flow, which would make this a setup
change and nothing more. If it does change the flow, this stops being a picker and starts
being a second scenario's worth of handlers, and should be re-scoped before it is built.

**What the capture has to answer**, same three questions as the agenda:

- are scenarios shown as **named tiles or artwork**, and is the name readable text;
- does the list **scroll**, and how many are visible at once;
- is a scenario **selected then confirmed with Next**, or does picking one advance by
  itself -- which decides whether `handle_next` gains a branch or is replaced here.

The same refusal rule as the agenda: a configured scenario that cannot be found must stop
rather than press Next and inherit, because inheriting is the exact failure this removes.

**The wider observation, not a request:** `SCENARIO_SELECT`, `TRAINEE_SELECT` and
`LEGACY_SELECT` are all `handle_next`, so the trainee and the inherit are inherited the
same way. Only the scenario has been asked for. Noted because whatever shape the scenario
picker takes is the shape the other two would want, and it is cheaper to notice that
before building the first one than after.

### Race agenda selection

*Asked 2026-09-04. The same shape as the scenario above and the deck below; all three
inherit rather than choose, and one capture session covers all of them.*

`handle_my_agendas` presses `load_list_btn.png` -- the first Load button the matcher
finds, whichever agenda that happens to belong to:

    if _click(f"{ASSETS}/load_list_btn.png"):
      state.agenda_loaded = True

So the bot races whatever agenda sits at the top of the saved list. That is fine while
there is one, and silently wrong the moment there are several: an agenda added, renamed
or reordered by hand between sessions changes every career after it, and nothing in the
log says which one was loaded -- it only says "Race agenda loaded."

The want is to name the agenda in config and load that one, the way the deck should be
named rather than inherited.

**What is missing is a capture of the My Agendas list**, and it decides the whole design:

- whether saved agendas carry **readable names** or only slots, which is the difference
  between matching text and counting rows;
- **how many are shown at once**, and whether the list scrolls -- if it does, this needs
  the same overlap discipline as the borrow list, and that is now a solved problem rather
  than a new one (see the borrow scroll entry under Done);
- whether each row has its **own Load button** or a select-then-load pair, which decides
  whether a row is clicked or matched-then-clicked.

Reading a name is likely the cheap part: `MY_AGENDAS` already identifies by
`my_agendas_title.png`, so the screen is known; what is unknown is what a row looks like.

**Two things worth settling in the same pass**, because they are the failure modes rather
than the happy path:

- **A named agenda that is not there.** Falling through to the first one is how you get a
  career raced on the wrong schedule without noticing. Refusing to start is the better
  answer, and it matches what an unconfigured borrow card already does.
- **The existing warning is not enough.** `handle_my_agendas` already warns when it finds
  no Load button at all and continues anyway. That is right for "there are no saved
  agendas"; it is wrong for "the one you asked for is missing", and the two need
  separating.

A config field goes in the `independent_training` block beside the other per-career setup
-- and remember it needs all five places, `reload_config()` included.

### ~~Support-card deck selection~~ — **done 2026-09-17**

*Built 2026-09-17:* `independent_training.deck` (1-10, 0 = leave it) and `deck_name`, which
overrides the number -- "Support Deck" in the UI. The pencil turned out to be a rename
dialog (1-10 characters), not a picker: decks are a looping carousel of ten on Support
Formation itself, arrows either side, a lit page dot per deck. The number is read from the
dot, sampled at each dot's own centre because the scenario's background shows through
between them (Grand Concert's glowsticks are green), and turned the shorter way; the name
is OCR'd off the green bar and matched exactly after normalising, since "Deck 1" is most of
"Deck 10". Chosen before the friend slot is filled. Not found in 20 turns stops the bot.
Verified live by number and by name. **Not yet seen: a deck with a custom name** -- none on
this account were renamed, so that path is tested only against the default names.
`devtools/check_deck_select.py`, 22 mutations caught.

*Asked 2026-08-28.* Pick a named deck rather than using whichever one the game happens to
have loaded. Today the bot takes what is on screen, so a deck changed by hand between
sessions silently changes every career after it.

The deck sits on the Support Formation screen — the one the bot already borrows a card
from — in a green header reading "Deck 9", with a pencil beside it at roughly (682, 205)
and a Copy button to its right. The name is plain text on a flat green bar, so reading
which deck is loaded should be about as easy as the TP figure. Treat that coordinate as
unmeasured: it was paced off a scaled crop rather than the capture.

What is missing is a capture of what the pencil opens. Everything else follows from it —
whether decks are picked from a list or cycled, whether they are named or only numbered,
and whether the screen returns to Support Formation by itself. Worth grabbing before this
is planned properly. A config field for the wanted deck goes in the `independent_training`
block alongside the other per-career setup.

*Third of the inherit-rather-than-choose family, with Scenario selection and Race agenda
selection above: the same failure -- the bot takes whatever is already there -- and the
same fix. One capture session covers all three, and they sit within a few screens of each
other in the career setup flow.*

---

## Wanted, not yet analysed

### What a frame costs is where its answer sits in SCREEN_ORDER

*Measured 2026-09-09, looking for something Phase 6 could usefully do and finding this
instead.* `identify_screen` walks `SCREEN_ORDER` and stops at the first match, so a frame
costs every spec tried before the one that answers -- about 34-50ms each, on 81 specs:

| | depth | cost |
| --- | --- | --- |
| `date_changed` | #1 | 17 ms |
| `title_screen` | #9 | 473 ms |
| `home` | #35 | 1120 ms |
| `learn` | #42 | 1245 ms |
| nothing matches | 81 | ~2.8 s |

This is the bot's largest CPU cost by a wide margin -- one frame at `home` costs more than
an entire skill survey does -- and it is paid on every pass of the loop. `py
devtools/measure_footprint.py --profile` reproduces the table.

**The obvious fix is not available.** Ordering by how often a screen is visited would put
`home` near the front, but `SCREEN_ORDER` is specific-before-generic *because that
ordering is what resolves ambiguous screens*; sorting it by frequency trades correctness
for speed, which is the wrong way round. Any real answer has to keep the order and make
the walk cheaper -- a cheap pre-filter that rules specs out before a full match, hoisting
the per-spec setup out of the loop, or reusing work across the specs that share a search
region. None of that is designed, which is why this is here rather than in Ready to build.

Worth knowing before starting: capping cv2's thread pool does nothing (1285ms at 16
threads, 1248ms at 4), so this is single-threaded work and the win has to come from doing
less of it, not from spreading it.


*Asked 2026-08-30.* Two housekeeping chores on the home screen, both currently done by
hand:

- ~~Collect mission rewards automatically~~ — **done 2026-09-03**, as a task. Walks all
  four tabs pressing Collect All, because a greyed Collect All is a no-op and four presses
  are cheaper and more certain than reading four small red count bubbles.
- ~~Collect gifts from the main menu's present box~~ — **done 2026-09-03**, as a task. One
  press takes up to a hundred gifts.

Settled: they are tasks, not a "tidy the home screen" pass. Each retries independently, so
one failing does not silence the other, and each gets its own switch.

*Asked 2026-09-03. Phase 1 done 2026-09-20.* **Pull on start, so the bot always runs the
latest version.**

**Where it stands.** The "check and say" this entry recommended is built, as phase 1 of a
four-phase updater. `core/updates.py` asks GitHub for `version.txt` and `CHANGELOG.md`
over HTTPS -- never git, so it cannot touch the checkout -- with a 3-second timeout, an
answer cached six hours in `config/update_check.json` and shared between instances. The
web UI shows a banner, the console says one line at startup, Setup has the switch
(`auto_check_updates`) and a **Check now** button. Offline is a shrug that keeps
yesterday's answer. Covered by `devtools/check_updater.py`, 26 mutations, all caught.

Version discipline came with it: `version.txt` had read 1.0.0 across its whole history,
so the number told a user nothing. It now moves with every user-facing change, beside a
`CHANGELOG.md` section, enforced by `devtools/check_version_bump.py`.

**Known, and blocking the feature from doing anything for users yet:** the GitHub repo is
private, so the raw fetch 404s for everyone. The check says so in as many words rather
than blaming the network, and announces nothing. Nothing else to do here -- it starts
working the day the repo is public.

**The phases not built.** Decide after living with phase 1, which is the point of having
built it first:

- *Phase 2, update on request.* `POST /update/apply` behind a button: preflight (a git
  checkout, on main, clean tree, bot idle, no other instance live -- each refused with
  the reason), record HEAD for rollback, `git pull --ff-only`, reinstall dependencies
  only when `requirements.txt` moved.
- *Phase 3, restart.* An update must be followed by one, since new Python routes do not
  survive a refresh. Waits on the bot-restart work; until then phase 2 would end by
  telling the user to close the window and run `start.bat`.
- *Phase 4, rollback.* Check out the recorded sha, and tag each version `v1.2.0` so "the
  build that worked last night" is a name rather than archaeology.

The original entry, kept because its hazards are still the reasons phases 2-4 are
careful:

*Asked 2026-09-03.* **Pull on start, so the bot always runs the latest version.**

The want is real: the repo moves most days, and there is currently nothing that tells you
you are three commits behind except noticing a bug that was fixed yesterday.

Not yet designed, because a pull that runs immediately afterwards inverts who decides when
a change goes live, and several of the failure modes are quiet ones:

- **A dirty tree.** `config.json` is ignored, but any edited tracked file -- a threshold
  nudged during a debugging session, a print left in -- turns the pull into a conflict.
  Refusing to start is the *good* outcome; starting on a half-merged tree is the bad one,
  and it is the one that needs writing carefully.
- **Dependencies move, the venv does not.** A pull that changes `requirements.txt` leaves
  the bot importing against stale packages, and that surfaces as an unrelated crash three
  screens later.
- **Branch and remote state.** Pulling is wrong on a feature branch and wrong when
  detached, and neither is unusual on this machine.
- **Offline has to be a no-op, not a wait.** Startup cannot block on the network, so the
  check needs a short timeout and a shrug.
- **Rollback gets harder, not easier.** "Run the version that worked last night" stops
  being something you arrange in advance and becomes something you do under pressure,
  usually while the thing is misbehaving.

Worth weighing first: **check and say, rather than pull.** Compare against the remote at
startup and print `3 commits behind -- git pull to update`, leaving the decision where it
is now. That is most of the value, none of the hazards above, and it is a few lines
against a feature that needs a config switch, a dirty-tree guard, a dependency check and
a way to turn it off when it goes wrong. If the answer after using that for a week is
still "just pull it", the pull is a small change on top.


*Asked 2026-09-04.* **One borrow card, or a preferred card with fallbacks.**

The picker writes `{ borrow_cards: [path] }`, so choosing a card truncates the stored list
to one entry. That is what was asked for -- you cannot borrow more than one card -- but it
is a statement about how many can be *borrowed*, not how many can be *looked for*.
`find_card` already walks the configured list in order and stops at the first hit, so a
second entry costs nothing and changes nothing about the borrow itself. It only decides
what happens when the preferred card is not among the friends on offer, where the answer
today is "refresh until it is".

The truncation also happens silently to a config that already had several entries, and a
comment in `IndependentSection.tsx` currently claims the opposite -- that a hand-edited
multi-entry config keeps working as a fallback chain. The loader honours it; the UI
discards it on the next save.

Not built because it is a UI question, not a matcher one: whether the panel shows one
choice plus an optional ordered "if not available" list, or stays strictly single.


*Asked 2026-09-06.* **General navigation recovery: back out of a bad position with the
Android back button, before reaching for a restart.**

Today the recovery ladder has two rungs and a long drop between them. A screen the bot
cannot place is tolerated for `STUCK_FRAME_LIMIT` frames (~40 seconds), and then the only
move left is to restart the whole game -- which costs the splash, the title, however many
login interstitials are owed, and the walk back to a career. Measured on 2026-09-06 that
is the better part of a minute to the title screen alone, before any of the walk. Most of
the positions that earn it are a menu or two deep, and a person would get out with two
taps of Back.

`input keyevent 4` is one line now: `utils/adb_actions.py` grew a generic `shell()` when
restarting was built, so there is no new plumbing. Nothing in the repo sends a key event
today -- `back_btn.png` is the game's own on-screen button, matched by template, and
unrelated.

**Measure before designing, because the premise is not established.** Unity games
frequently ignore the Android back button outright, and this one has never been asked. The
first task is a sweep on a live device: press it on each screen class in the corpus and
record what happens -- nothing, a step back, or a modal. That result decides whether the
rest of this is worth writing, and it is an hour with `adb_probe`.

**Why it is not simply "press Back until something is recognised".** The bot's whole design
is identify-then-act. Pressing Back is acting without knowing where you are, and the
hazards are the quiet ones:

- **A back press can be destructive.** Mid-race, mid-training, or on a confirmation the
  bot did not open, Back may abandon rather than retreat. The screens where that is true
  are exactly the screens the bot is most likely to be lost on, because they are the ones
  with the most modals.
- **A back press that does nothing looks identical to one that did something invisible.**
  The only way to tell is to re-identify after each press, which bounds how fast this can
  walk and means it must never press twice without looking.
- **On the home screen Back often means "quit the game?"** -- a dialog the bot would then
  be lost on, one press deeper than where it started.
- **It trades a diagnosable stop for a silent wander.** This is the same argument the
  restart's same-kind guard makes and it applies with more force here, because a wander
  leaves no incident screenshot of where it ended up. `save_incident_image` has to fire
  before the first press, not after the last.
- **It is meaningless when the bot is not in the game at all.** 2026-09-06 ended with the
  emulator on its launcher; Back there does nothing useful. Cheap to rule out now that
  `_learn_game_package` knows what the game is -- compare it against the foreground before
  pressing anything.

**Shape, if the sweep says yes.** A bounded walk between the two existing rungs: press
Back, wait, re-identify; stop on a screen the bot can navigate from; give up after a small
number of presses and fall through to the restart that would have happened anyway. It
wants its own `recoverable` kind so the same-kind guard keeps working -- "backed out and
ended up somewhere unrecognised twice" must not be mistaken for a fresh problem.

**Not for every stuck.** Only the `unrecognised_screen` family, where the game is
responsive and the bot is merely lost. Explicitly not `countdown_stalled`: on 2026-09-06
that turned out to be the game wedged on a black screen with its process alive, and no
number of back presses moves a frozen renderer. A force-stop did.

---

## Pinned by the redesign

### Waiting for TP instead of stopping

*Analysed 2026-08-30 — do not re-derive.* With TP refill switched off and not enough TP
for a career, wait for natural regen rather than stopping: 1 TP per 10 minutes, so 0 → 30
is 300 minutes, and spend RP on Team Trials while waiting.

Today that path is a single `_stop` in `handle_home`, at the `blocked = "TP refill is
switched off"` branch. Replacing it is roughly 70 lines. What the analysis turned up:

- **The real obstacle is `MAX_ACTIONS_PER_RUN = 400`.** `Screen.HOME` is not in
  `RECOVERY_SCREENS`, so every frame spent waiting is charged as an action, and at
  `LOOP_POLL_SECONDS = 1.0` the budget is gone in under seven minutes — the bot would die
  of "exceeded 400 actions" nearly five hours early. So the wait has to sleep rather than
  poll (once a minute, ~300 wakes instead of ~18,000, which also keeps debug images from
  reaching tens of GB), and waiting frames have to be exempt from the budget. Sleep in
  chunks so `shift+<hotkey>` and connection loss are still noticed.
- **Use `time.sleep`, not `utils.tools.sleep`**, which multiplies by
  `SLEEP_TIME_MULTIPLIER` and would silently turn five hours into two and a half for
  anyone running 0.5.
- **Team Trials during the wait is free.** `handle_home` already calls
  `_run_team_trials_if_due`, and the 30-minute stand-down (c9de98b) gives it the right
  cadence with no extra work.
- **Bounds worth having:** stop at once if `maximum < cost`, since the wait can never
  succeed; a ceiling on `(cost - current) × 10 min` so a misread cannot hang forever; and
  one log line plus a Discord notice up front, or an unattended user cannot tell waiting
  from hung.
- **Suggested: no new config key** — make it the behaviour, since stopping is strictly
  worse unattended and `shift+<hotkey>` already ends a session cleanly. Not agreed either
  way.
- **Genuinely untested, not merely unwritten:** nothing has ever had this bot idle at home
  for hours, so a midnight JST rollover or an idle disconnect mid-wait would be the first
  live exercise of the daily-reset chain.

### Waiting out a session signed in elsewhere

*Done 2026-09-03.* A hold over the whole queue rather than a deferral on each task:
nothing can run while another device owns the session, so it is one fact about the queue,
and a task added later is covered by it without anyone remembering to add it to a list.
`independent_training.session_conflict_wait_minutes`, default 60, 0 restores the stop.

The gate that matters is `handle_title_screen`, not the error dialog. The dialog offers
one button and it goes to the title screen, so there is nowhere else to be held; what
must not happen is the *next* tap, which is the one that logs back in and signs the other
device out.

### ~~Daily races~~ and Legend Races in the main loop

*Asked 2026-08-28. Half-answered 2026-09-03: **daily races shipped** as their own task
with their own screens and handlers -- see Ready to build. It settled the definition
question at the bottom of this entry too: "daily races" means the Daily Races that reset
each day, not Champions Meeting. What is left here is **Legend Races**, and the
`auto_misc.py` assessment below, which still applies to them.*

*`auto_misc.py` and the 26 templates only it used were removed in 1.0.2 (2026-09-23), for
now. The assessment below describes the file as it was; it and its assets are in commit
`7a59a31` (1.0.1), which is where to take them from if this is picked up.*

As options alongside careers. Both are "another kind of work in the
loop", which is exactly what the redesign is about, so settle that first. The groundwork
below holds whatever shape it takes, because it is about what `auto_misc.py` does and does
not give you.

`auto_misc.py` is a standalone entry point with working loops for Team Trials
(`--tt hard|medium|easy`), Legend Races (`--lr`) and Champions Meeting (`--cm`), each with
its own template set — `tt_race`, `tt_gift`, `tt_select_opponent`, `tt_refresh_btn`,
`tt_see_all`, `tt_team_race`; `lr_ticket` and its confirm and next buttons; the `cm_*`
group. It survived the career-code pruning because it needs none of those modules. So this
is integration rather than a build: a config switch per task, a decision about when each
runs (between careers, or when TP is out and a refill is not wanted), and screen specs so
`identify_screen` owns those screens instead of `auto_misc` doing its own matching.

**What transfers is the templates and the click order, not the control flow.** Read
through on 2026-08-28: the Team Trials path covers the happy case — enter, `tt_team_race`,
`tt_see_all`, `tt_race`, `tt_gift`, race via the shared `do_race` — but it is a "click
whatever is on screen" loop with five gaps that matter unattended. Opponents are chosen by
fixed coordinates rather than matching, and it assumes three rows. It never identifies a
screen, so there is no per-screen recovery. It has none of the connection-loss,
date-changed or title-screen handling that lives in `independent_screens.py`. It calls
`quit()` when stuck, a hard process exit that would kill the whole run rather than
stopping cleanly. And nothing counts the day's three races — it relies on buttons
vanishing. Assume a rewrite as screens and handlers, reusing the assets.

**~~"Daily races" needs defining.~~** *Settled 2026-09-03: the Daily Races that reset each
day, not Champions Meeting. Built from fresh captures rather than from `auto_misc.py`,
which has nothing for them -- the word "daily" does not appear in it at all.*

---

## Built — verification status

### The template census: built 2026-09-02, and what it found

`devtools/adb_probe.py census`. It answers three questions, and only the first was ever
asked by hand:

1. **Does each screen's own anchor clear its threshold on its own capture?** The stuck
   kind of failure — the one that ends a run and gets noticed.
2. **Does any anchor score high on a capture that is not its own, *and* get checked
   first?** Ordering already settles most overlaps, so ranking without it buries the real
   entries under a page of noise. This kind is worse than the stuck kind: the loop does
   not stop, it acts on the wrong screen and keeps going.
3. **Does a screen clear its own threshold by less than 0.05?** Neither of the others
   sees this, because a screen that still clears looks fine to both — even at 0.017 with
   something scoring higher queued behind it.

Against 56 ADB captures and 70 screens it now reports nothing in category 1, nothing in
category 2 that is not structural, and nothing in category 3 with a rival queued behind
it — everything it found on the first run has been fixed (see Done). Six screens still
clear by under 0.05, but with nothing behind them, so drift there strands visibly instead
of routing the loop somewhere wrong.

It scores specs in a thread pool: 71s against 5m12s serial, byte-identical output, since
`matchTemplate` drops the GIL the same way the replay suite exploits. It understands
`ScreenSpec.scorer`, so a screen decided by colour rather than by a template reports its
real margin instead of 0.000.

The probe is also the fastest way to work a live screen: `probe` names what identified,
what nearly did, and what every click target on it would score; `save` writes a capture
straight into the ADB set; `click` and `tap` drive the emulator in the desktop coordinate
frame constants are authored in, so a number that works there pastes straight in.

### ADB operation: built, hardened, and live-verified 2026-09-01

The on-hold item below finally waited long enough to be superseded: the bot runs
unattended against a BlueStacks emulator (127.0.0.1:5555, 800x1080, 240dpi) while the PC
stays usable, across a full career on the feature/adb-hardening branch. What landed:

- The device layer (`utils/adb_actions.py`): reconnect-retry clicks and swipes,
  landscape rotation, empty-region and dimension warnings, a screenshot cache with
  per-consumer flushes, and an out-of-bounds region warning.
- Coordinates: `adjust_constants_x_coords` now covers `_POS` and `_BBOXES` (a career
  button was clicking 155px off because `_POS` was never shifted); a 128-constant bounds
  audit backs it. Position constants for new screens are authored in the desktop frame
  and rebased by the same call.
- Scrolling, measured rather than assumed (`devtools/probe_fling_threshold.py`): the
  real fling threshold sits near 100px/s — above the textbook 75 — and the glide just
  under it is a few pixels. Notch drags run 130px over 1.15s (~113px/s, ~18px glide),
  absorbed by the skill survey's ~345px parseable band and the training log's overlap.
- The skill survey cycle: the OCR parse fans across a thread pool, the settle frame is
  reused for the next parse and the end comparison, and the cycle dropped by roughly a
  third. The bottleneck that remains is EasyOCR on the CPU-only torch build — this
  machine has no NVIDIA GPU.
- Screen coverage: nine screens surfaced one stuck session at a time — the Recover TP
  list, TT select-opponent, the TT quick-mode pills, the Complete Career Finish modal,
  Keep Sparks, the Daily Sale announcement and the epithet award window (both new with
  the game's 2025-09 update), My Agendas, and the Proceed? dialog — each fixed with a
  measured structural anchor or threshold and locked into the ADB replay suite, now
  13 captures, 13/13 passing beside 92/92 desktop references.
- The carat read records nothing rather than a false zero (a drifting icon match once
  clipped "x10" into "x0"), retries at small nudges on the same frame, and the log scan
  step cap is 15.

Residual: three templates are cropped from emulator frames (the epithet checkbox row,
the Daily Sale title, the Proceed? tail) because no desktop capture of those windows
exists — see Blocked on a capture. This entry also closes the unfocused-window research
item below: the goal was a usable PC during a career, and the emulator route delivers it.

### Team Trials: verified live, 2026-09-01

Built 2026-08-29 (eba3f70, 3c41193) and now proven: the logs show 19 visits, runs of up to
five races, the new-high-score screen handled twice, the tallying stand-down firing four
times, and clean exits to home.

The last `tt_home_btn not found` failure is at log line 878 of 22,000 — before f651c45
trimmed that asset by 8 rows. Until then the exit click missed, and the handler re-entered
from `tt_lobby` on every pass; that is what the 269 failures and the repeated "done for
now" lines early in the log are. Fixed. Do not re-investigate.

Kept because it is what the faults looked like: live runs stopped on five separate faults
before this worked. A blank No button and a phantom RP reading (565db1c). The Home tab
template cut from the home screen, where that tab is active and blue, then pressed on the
lobby, where it is inactive, angled and grey — one template cannot be both (087f832). The
no-rematch result anchored on the results grid, whose numbers change every race (6ab4520). And a screen nobody had
seen — the "NEW HIGH SCORE!" celebration that lands between the race and its result when
the team beats its own best, which read as `unknown` until the stuck detector gave up.

Two failure modes, and both keep recurring. The first three were crops containing
something transient — a chibi, a tab state, a score — which the replay harness only catches
when every button a handler can press is registered in `CLICK_TARGETS`. Register them. The
fourth was a screen nobody had seen, and no harness can catch that; it needs a capture.
Expect more. This is not a Team Trials problem: the game puts conditional screens — a
record, a reward, an offer, a prompt — anywhere something optional happened, and career
teardown had one too, the Follow Trainer prompt among the reward screens when the borrowed
card came from a non-friend (added 2026-08-31). The prediction held on ADB too: the
select-opponent and quick-mode screens both needed 2026-09-01 fixes of the same species
(see the ADB entry above).

### The daily-reset recovery chain: untested, not missing anything

Fully captured — Date Changed, title screen, Post-Career home, the Independent Training
panel and the Training Log all have references and all identify. What it has never had is a
live run: those handlers have fired zero times in the logs, because the reset that prompted
them happened before the fix existed.

Do not record this as "needs a screenshot". It was once written that way, on a guess that
the game shows login bonuses after re-entry, which nobody has observed. If something
unrecognised does appear, recovery is armed, so the bot stops cleanly after ~10 minutes
rather than hanging.

### Carats earned: built 2026-08-27, one gap left

They are **not** on the Rewards screen — that was the wrong place and cost a long search.
They are on the Training Log's *Career* page: from the Overview page, where stats and fans
are already read, click the arrow beside the title, then scroll to the bottom for an "Items
Obtained" grid. The icon is matched rather than counted to, since the grid varies per
career. Awards only ever come in fives, so a count that is not a multiple of 5 is refused
as a misread.

Residual risk: only single- and double-digit awards (`x5`, `x10`) have been read; the
double-digit case surfaced on ADB and exposed a real misread — the quantity box's bottom
edge clipped "x10" into "x0", which slipped past the multiple-of-5 guard — now fixed
(zero is refused, the read retries at small vertical nudges, and the box is the
desktop-proven geometry). A triple-digit award is still unseen.

---

## On hold by choice

### Showing time remaining in the Overview, not time on task

*Asked and analysed 2026-09-03; pinned the same day — do not re-derive.* The Overview's
running row counts from when the bot adopted the task, so a career resumed at 24 minutes
remaining reads "for 2m". The question raised was whether to derive elapsed from the
countdown instead.

Settled: **no to derived elapsed, yes to showing the countdown itself, if built.**
Computing `50min - remaining` takes a number the bot actually read and multiplies an
assumption onto it to produce one that answers less; "when is this done" is the question
the column is read for, and remaining answers it directly.

What it needs, and the three things that shape it:

- The countdown is only readable on `training_in_progress`. That is ~49 of the ~50.2
  minutes, so it covers nearly all of a career, but it goes blind through the teardown
  and Team Trials has no countdown at all — elapsed stays the fallback, so the column
  carries two meanings and the wording ("33m left" against "for 2m") has to say which.
- `read_remaining_seconds` returns None on a bad read *on purpose*: the caller treats a
  countdown that stops decreasing as a stalled career, so a wrong number is much worse
  than none. The panel has to render "not known" rather than inventing a fallback.
- No extra OCR: the wait loop already reads it every poll, so this is caching the last
  value beside `_entered` and exposing it in `/bot/status`.

Not a bug in the meantime. Labelled "for 3m" the current number honestly means time on
task; it misleads only if read as career progress.

### Reaching the web UI from other machines

*Asked about 2026-08-27.* The app side is one line — `host = "127.0.0.1"` in `main.py`
becomes `"0.0.0.0"` — and CORS is not in the way, because the page is served by the same
origin it calls. Windows Firewall prompts once.

The reason it is not already done: **the server has no authentication of any kind.** Anyone
who can reach the port can rewrite the config and skill lists, wipe the run history via
`/stats/reset`, and read the Discord webhook URL out of `/config`, which is a credential.
Fine on a home LAN, not on a shared one, and never port-forwarded as it stands. If built:
make it a config option defaulting to off, and add shared-token middleware (~30 lines)
before it is used anywhere untrusted.

*2026-09-17:* one part of this is closed on localhost too. Writes carrying a foreign
`Origin` (or `Sec-Fetch-Site: cross-site`) are now refused with a 403, so a web page in
another tab can no longer start or stop the bot through body-less POSTs that CORS never
stopped. That is not authentication -- anything that can send its own headers still gets
in -- so the token is still needed before the port leaves this machine.

### Replacing EasyOCR

*First measured 2026-09-02; every number below re-measured 2026-09-05 — do not
re-derive.* `devtools/bench_ocr.py` runs each engine through the bot's own readers
against 18 fields whose true values were read off the captures by eye. Numbers are from
this machine (Radeon 780M, no NVIDIA), Tesseract 5.4.0, `--repeat 8`, on an otherwise
idle machine. Two things about the conditions, because both move the numbers: do not
read anything off a `--repeat 2` run, which is too short and swings by a third; and a
running emulator costs 10-15% of the CPU
and inflates every absolute figure below by roughly a sixth, while barely touching the
ratios (EasyOCR loses the most to contention, so it narrows the gap rather than widening
it).

| engine | correct | mean ms/field | a full screen's fields |
| --- | --- | --- | --- |
| EasyOCR | **18/18** | 39.6 | 712 ms |
| Tesseract via pytesseract | 10/18 | 127.0 | 2286 ms |
| Tesseract via the C API | 10/18 | **4.1** | 73 ms |
| RapidOCR, CPU | **18/18** | 11.3 | 204 ms |
| RapidOCR, DirectML | **18/18** | 11.2 | 202 ms |

**The subprocess is gone, and it was the whole speed story.** The 2026-09-02 entry
concluded Tesseract's speed was real but unreachable: 94% of a pytesseract call is
spawning `tesseract.exe`, and `tesserocr` needs a compiled wheel that does not exist for
cp313. The second half of that is wrong. The Windows installer ships
`libtesseract-5.dll` beside the exe, and its C API is a dozen `ctypes` declarations — no
wheel, no toolchain, no build. In process the same engine costs **4.1 ms a field against
127.0**, a 31x drop, and the old estimate of the engine's own speed (13.9 ms, derived by
subtracting subprocess overhead) was itself far too pessimistic.

**It does not matter, because the accuracy did not move.** `tesseract-capi` misses the
same 8 fields as `pytesseract`, character for character, down to reading sprint's F as S
— which is also the cross-check that the ctypes binding is faithful rather than subtly
misconfigured. All 8 are aptitude cells: coloured, outlined letters on a coloured ground.
Nearly ten times EasyOCR's speed while wrong on the fields the rating solver depends on
is not a trade worth making, and that was the 2026-09-02 conclusion too. It still holds.

**RapidOCR is the candidate that actually wins.** PP-OCR models under onnxruntime:
**18/18, matching EasyOCR field for field, at 3.5x the speed** and with no torch in the
process. It is the first engine measured here that is both faster and no less accurate.

Two things about it that are not obvious from the numbers:

- **Recognition only.** Detection is disabled not to save time but because it is *wrong*
  on these crops — on the skill-points cell it finds no box at all and the field reads
  empty, and it costs 150–330 ms when it does run. The cells are already a single cropped
  line, which is exactly what the recognition model alone expects. `Global.use_det: false`.
- **There is no allowlist.** EasyOCR and Tesseract both constrain the decode; PP-OCR can
  only have its output filtered afterwards, which is strictly weaker. It scored 18/18
  regardless, but the readers leaning hardest on `allowlist` are the ones to watch first
  if this is adopted.

**DirectML buys nothing, and that was verified rather than assumed.** It comes out a
consistent 1-3% ahead of the same models on the CPU, which is not a reason to take a GPU
dependency. The sessions genuinely run on `DmlExecutionProvider`, checked by
spying on `InferenceSession.get_providers()` rather than by trusting the config flag, so
this is a real measurement and not a silent fallback to CPU. On an integrated Radeon 780M
the per-dispatch overhead on a rec-only input cancels the compute win. A discrete GPU
might change that; nothing else on this list would.

**Skill names: measured 2026-09-05, and they are what nearly stopped the swap.** Swept
off a live buy list (66 distinct name crops, whole list, nothing bought), read through
`_enhance_for_ocr` and `canonical_skill_name` exactly as the survey does:

| engine | ms/name | resolves to a real skill |
| --- | --- | --- |
| EasyOCR, allowlist | 165.2 | 66/66 |
| RapidOCR, rec-only | **19.3** | 66/66 |
| RapidOCR, det+rec | 601.1 | 65/66 |

Read the speed column and rec-only wins by 8.6x. It is the wrong column.

**RapidOCR cannot see the tier glyph, and resolves 13 of 66 rows to a different real
skill.** The game marks skill tiers with a trailing `○`, `◎` or `×` — 153 of the 704
names in the index carry one. On every row that has one, RapidOCR silently drops it and
`canonical_skill_name` then resolves the row to its *untiered sibling*, confidently and
as a positive match. Verified by eye against the crops in
`references/independent_training_adb/skill_names/`: the circle is unambiguously on
screen, EasyOCR reads it (as the digit `0`, which `_split_ocr_tier` decodes), RapidOCR
emits nothing at 0.98-1.00 confidence.

Ruled out, so do not re-check:

- **Not the allowlist post-filter.** `○` (U+25CB) is in `skill_name_allowlist()`, and the
  raw unfiltered output has no circle either.
- **Not the model's charset.** All 14 symbols the game uses are in
  `PP-OCRv6_rec_small`'s 18,708-character set — `○◎★☆♡♪∞∴∀αéó×—`, every one of them. It
  can represent the glyph; it simply never predicts it.
- **Not preprocessing.** Five variants (the bot's own enhance, raw crop, 2x, greyscale
  3x, right-padded) miss it on all four tiered rows at high confidence.
- **Not detection.** det+rec drops it too, is 3.6x *slower* than EasyOCR rather than
  faster, and additionally scrambled one row's word order (`'t Positioning Prudent'`).

This failure is worse than a plain misread. `_split_ocr_tier` already documents that
EasyOCR loses `◎` and returns tier `None` rather than guessing, precisely because
guessing "made every unreadable glyph look like an upgrade the user had not asked for,
and produced a phantom second entry for a row already recorded at its real tier".
RapidOCR does not return `None` — it returns a confident wrong sibling.

**What unblocked it:** reading the tier off the pixels rather than out of the text. Not
by template match, as first sketched — the mark is separable on plain geometry, being
hollow where a letter is solid, square where a letter is not, and standing off behind a
space, with hole count telling the tiers apart. `core.independent_skill.tier_glyph`, and
`devtools/check_tier_glyph.py` for what it cost to get right.

**Where that left it: swapped, 2026-09-05.** RapidOCR is the engine now. The tier glyph
was the one blocker and it is not an OCR problem at all -- `tier_glyph` reads the mark
off the pixels (`devtools/check_tier_glyph.py`), which fixes two rows of that 66 that
*EasyOCR* was also losing, so the skill path came out better than it went in. Both
engines then agreed 66/66 on the same sweep.

What the swap actually involved, since the file count understates it:

- **Every call site needed a detection decision, and that is the whole of the work.**
  Recognition-only is where the speed is, and it does not merely run slower on a region
  holding two lines, it returns nonsense -- the borrow list's title-and-character block
  comes back as `'Touching lves dLLuck!]'`. So detection is the *default* in
  `core/ocr.py`, and `use_recognize=True` opts in. A reader nobody has thought about is
  then slow rather than wrong. Nine of the eleven sites are single cells and opt in;
  the borrow row band and the Daily Sale body (158px tall) keep detection.
- **Test doubles had to move with it.** `replay_independent_skills`'s two `fake_ocr`
  stubs still called the detection path after the real `_ocr` stopped doing so, and
  detection finds no box at all in a crop that small -- the double failed where the code
  it stood in for succeeded. Same class of bug in `bench_ocr`'s record-line closure.
- **`allowlist` is weaker than it was**, and permanently. EasyOCR and Tesseract constrain
  the decode; PP-OCR can only filter the output. Nothing measured regressed, but a reader
  that leans on the allowlist to force an ambiguous glyph is leaning on nothing now.

**Install: 1.9 GB of site-packages down to 455 MB**, verified by building a fresh venv
from the new `requirements.txt` and running the suites in it. Seventeen packages left
with torch and easyocr; Shapely, pyclipper, sympy and mpmath *look* like they should have
gone with them and must not -- rapidocr and onnxruntime pull them in too, which the fresh
venv is how we know. That saving is per instance, so it is the multi-instance work that
collects on it.

**The double circle, once a real one turned up (2026-09-05, third run).** It reads
nothing like the drawing of one that stood in for it. A drawn double has two clean rings
and reports two holes; on screen at 14px the rings *touch*, so the void between them
breaks into chips and the mark reports **six** holes at **0.49 fill** against a single
ring's 0.25-0.30. The first implementation refused it on both counts and the row resolved
to the untiered skill -- the exact failure the detector exists to stop, caught only
because a debug frame was read by eye.

So the rule is topological rather than a count: one hole is a circle, more than one is a
double, none is neither. Fill guards the one-ring case only, since a double is denser
than any single ring and the gate that keeps a bold capital O out would also refuse it.

The reason it took three runs to see one: **a double only appears after an upgrade.** The
buy list offers the next tier in place of the one just bought, so a row shows its double
only once its circle is gone. Nothing is wrong with the earlier runs' lists.

**Still open: the cross.** Read from the text, not the pixels. It is concave, so the
solidity gate that keeps a name's own star out cannot tell one from the filled star or
quaver that appear inside skill names -- and OCR reads it correctly as a plain `x`
anyway, so the text keeps that job. A real capture of one would let that be revisited;
`devtools/check_tier_glyph.py` is where the case belongs.

### Pruning phase 6

Trimming ~42 unused config keys and the career-era entries in `utils/constants.py` that
keep `assets/buttons|icons|ui` alive. Phases 1–5 are merged. Cosmetic, zero runtime cost,
and a config key lives in several places — recommended against.

### Deferred

Trainee name in run statistics. And `duration_seconds` being null for a career the bot did
not itself start, which is correct behaviour rather than a bug.

---

## Decided — do not revisit

**Skill buying maximises *rating*, not race strength.** These are Independent careers, so
how the trainee actually races is irrelevant. This is why gold skills get skipped: the
rating system prices a gold at ~1.6x a white while charging ~2.3x the points, so the solver
prefers breadth. Correct here, not a bug to fix.

**`references/score_calculation` stays untracked.** It holds ~4.4 MB of Uma Event Helper
Web (`optimizer.js`, `rating-shared.js`, `skills_all.json`, `uma_skills.csv`).
`references/` is otherwise a tracked directory, so `git add -A` would redistribute another
project's source from this repo. Only `data/uma_skills.csv`, the 175 KB table the runtime
needs, is committed.

---

## Done

Do not re-plan: skill blacklist, connection recovery, daily reset, post-career pickup, TP
refill, racing style, training focus, the Debug tab, input jitter, statistics, Discord
notifications with a Send Test button, career-code pruning phases 1–5, config auto-save,
badge-only skill decisions, negative ("Remove …") skill recognition, the confirmation-screen
TP read, `start.bat`, and the Mirako Machine web-UI rebrand (b16a9bb).

Done 2026-09-03, after the task queue merged. **The TP counter is read off the blue
channel** (b8eb677): greyscale flattens orange-on-white, so `36/100` came back as `7/0`
and `20/0`. Reported three times -- the third was the same bug still running, because the
user had restarted the *bot* and not the *process*, and Python loads modules at import.
**The `tp_too_low` dialog is recognised** (8c8fbbc): its anchor was still the desktop
client's wording and scored 0.494 on ADB, while both of its buttons had been matching at
0.961-0.982 the whole time. **A saved setting is applied, not just written down**
(06dda1c): `reload_config()` ran at process start and bot start only, so every toggle in
the web UI wrote to disk and changed nothing until a restart. **The leftover pass stops
surveying** (1b70234): `handle_learn` re-enters after every Confirm by design, so the
pass that buys nothing is the one that leaves -- and it was walking the whole list to
prove 59 points buys nothing, twice, for 840 captures and five minutes. It now remembers
the cheapest row it saw and the balance it saw it at. **The rewarded Team Trials opponent
is taken when one is offered** (53cffa7), as a toggle.

Done 2026-09-04. **Borrow cards are chosen from a window** (4958e14, b22e370) rather than
by typing a path into a text box, and the choice is one card rather than a ranked list,
since only one is ever borrowed. **The shipped defaults changed**: an empty skill priority
list instead of eight samples (d0f645f), and the twenty unique-inherit blacklist entries
the template was missing (f9f7d4b). **The Borrow Card list steps by a page it can see**
(840dd4f) -- the entry below is the one to read before touching any scrolling scan.

### The borrow scan was skipping a page at a time on ADB

*2026-09-04 -- do not re-derive.* `INDEPENDENT_BORROW_SCROLL_NOTCHES` was 6, correct for
the desktop wheel's ~75px notch: 450px against a 770px viewport, over two rows of
overlap, exactly as the constant's comment claimed. ADB has no wheel. `device_action.scroll`
emulates each notch as an `ADB_SCROLL_NOTCH_PX` drag -- 130px -- so the same 6 notches
travelled 780px, more than the whole visible list. Consecutive reads did not overlap; they
left a ~10px blind seam, and the scan only looks between steps.

Measured on a live friend list: stepping one notch at a time found the configured card at
notches 1-4, peaking at 0.974. The bot samples at 0, 6, 12 and never landed inside that
window, so the card scored 0.38 -- noise -- in all 54 captures of a full walk.

**Why it took a day to find.** Nothing reports it. `find_card` returns None, the scan logs
"no configured card in this list", and the handler refreshes forever -- which is the
correct behaviour, and the identical output, for a card that genuinely is not on offer.
The first diagnosis blamed the config, the second nearly blamed the template. Neither was
wrong about what it measured; both were measuring the wrong frame.

**The general lesson, which applies past this one list:** a notch is a different distance
on each platform, so any constant counted in notches needs the platform split the skill
survey already had. `devtools/check_borrow_scroll_overlap.py` enforces it for this list --
travel under a page, two rows of overlap, and a synthetic walk that reads every row.

Done and verified in a live run on 2026-08-27: `shift+<hotkey>` to stop once the career in
progress finishes (09edaec), the career-complete Discord message moving from the Training
Log to the home screen (470eab6), and a debug switch that opens Recover TP whatever the
balance and stops at the dialog rather than confirming (b5a6cc3). The TP refill chain
itself was exercised live the same day — started at TP 15, refilled, then started the
career.

Done 2026-09-02, all four found by the template census on the day it was built: the
Final Confirmation tabs now decided by which pill is green rather than by the word on it
(a `scorer` hook on `ScreenSpec`, 0.900–0.932 against 0.000 where the two text crops had
been separated by 0.024); emulator-cut second anchors for Borrow Card, Career Complete
and the career Training Log, each of which had been clearing by 0.017–0.039 with a
higher-scoring generic button queued behind it; `FINAL_CONFIRM_LINEUP_EXPANDED` moved off
the default threshold to 0.94, because a 44px chevron is nearly the same pixels whichever
way it points; and `TT_MATCHUP` re-anchored on its "Race 1:" heading, which retires the
last identification resting on the generic Next button — it and `POST_CAREER_NEXT` had
both scored exactly 0.959 on that frame off the same template.

Done 2026-09-01: the stat OCR sanity check (c7694ca) and the TP refill tally expiry
(1e4e1eb), which also closed a harness leak where `replay_independent_flow.py` wrote real
refills into `stats/pending.json` and billed them to the next real career.
