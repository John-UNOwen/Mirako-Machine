# Mirako Machine

Automation for Umamusume's **Independent Training**, the mode where the game plays a
career by itself over about fifty minutes. The bot handles setup, waits out the career,
spends skill points, collects rewards, and starts the next one. It can drive several
emulators at once from one web UI.

# ⚠️ USE IT AT YOUR OWN RISK ⚠️

We are not responsible for bans, losses or other issues caused by using this bot. See the
[License](./LICENSE) and the [License of Use and Disclaimer](./LICENSE_AND_DISCLAIMER.md).

New to the game? Learn its systems first: [game guides](./readmes/GUIDES.md).

## Features

**Careers, back to back**
- Picks the scenario, support deck, Training Focus, Racing Style and race agenda (by
  position or by name). If it can't find what you asked for, it stops instead of guessing.
- Uses the trainee and legacy already selected in game, so choose those yourself.
- Fills the friend slot from your borrow-card list (required: a career won't start without one).
- Runs a set number of careers, or 0 for no limit.

**Skill points**
- Buys from your priority list in order; a blacklist vetoes skills everywhere.
- Optionally spends leftovers, including a **Maximise rating** mode that scores skills
  against the trainee's aptitudes.

**Task queue**
- Runs Team Trials, daily collections and careers, whichever is due.
- Out of TP? It waits and fills the gap with other tasks instead of stopping.
- The **Overview** tab shows the queue; **Run now** skips a wait.

**Daily rewards**
- Collects mission rewards on every tab and empties the present box, and collects
  again whenever the icon's badge shows something new.

**Recovery**
- Reconnects through errors, daily resets and the title screen, then resumes.
- Restarts the game when stuck (with a limit, so it never loops).
- If the account is signed in elsewhere, it pauses for an hour instead of fighting over it.
- When it gives up, it stops cleanly and saves a screenshot to `logs/incidents/`.

**Optional (off by default)**
- **TP refill** with Toughness 30, carats, or both, plus a carat floor and a refill cap.
- **Team Trials** between careers, keeping a reserve of RP charges.

**Reporting**
- Statistics per career (`stats/runs.jsonl`), shown in the Statistics tab.
- Discord webhook for results and interruptions, and a sound when a session stops.

## Installation

### Requirements

- [Python 3.13](https://www.python.org/ftp/python/3.13.11/python-3.13.11-amd64.exe)
  (3.11 and 3.12 may work; 3.10 and 3.14 do not)

### Install and run

```
git clone https://github.com/John-UNOwen/Mirako-Machine.git
cd Mirako-Machine
```

Double-click **`start.bat`**. The first run sets up a private environment in `.venv`
(a few minutes); later runs start immediately. To uninstall, delete `.venv`.

<details>
<summary>From a terminal instead</summary>

`start.bat` accepts the same arguments as `main.py` (e.g. `start.bat --debug`). Or manually:

```
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```
</details>

### Updating

When a new version is out, a banner appears in the web UI. Press **Update**, then restart
`start.bat` when told. If the new version is worse, the same dialog offers **Go back**.

The update is refused if the bot is running, you've edited tracked files, you're not on
`main`, or you downloaded a zip instead of cloning.

To update by hand: close the bot, run `git pull` (after `git checkout main` if you rolled
back), then reinstall requirements if it fails to start.

Your settings, stats and logs are never touched by updates. See [CHANGELOG.md](./CHANGELOG.md)
for what changed.

## Before you start

In game:
- Turn off all confirmation pop-ups
- Set graphics to standard
- Start on the home screen (it can also resume a career or log in from the title screen)

**Emulator with ADB (recommended)**
- Display size 800x1080 (portrait), DPI 160 or higher (240 recommended)
- Find the ADB port (LDPlayer: 5555, MuMu 12: 16384, Nox: 62001, BlueStacks 5: Settings > Advanced)
- In the web UI's Setup tab, enable **Use ADB** and set Device ID to `address:port`
  (or pass `--use-adb <address:port>`)

**Emulator without ADB**
- 1920x1080 monitor, emulator on the primary monitor
- Display size 800x1080, DPI 160
- Set the window name in the config to the emulator's exact window title

**Steam**
- 1920x1080 monitor, game fullscreen on the primary monitor

## Usage

Open `http://127.0.0.1:8000/`. **Start**, **Stop** and **Finish & stop** are in the header.
Hotkeys work without the browser focused: `f1` starts/stops, `shift+f1` finishes the
current career and then stops.

### Tabs

- **Overview**: the task queue
- **Setup**: ADB, device, window name, sounds, update checks
- **Automation**: careers, focus, style, agenda, skill spending, borrow card, Discord,
  TP refill, Team Trials, daily collections
- **Skills**: priority list and blacklist
- **Statistics**: run history and totals
- **Debug**: dry-run switches (below)

Presets are saved in `config/*.json` and managed from the bar at the top of the page.
`sleep_time_multiplier` (click pacing) has no UI control; edit it in `config.json`.

### Multiple emulators

Each emulator needs about 1.6 GB of memory, so expect four or five at most.

1. Start each emulator at 800x1080.
2. In the web UI, press **+ Add instance**, name it, and pick its emulator.
3. It runs on the next port with the next hotkey (`f2` on 8001, `f3` on 8002, ...) and
   appears as a tab.

Each instance has its own settings, logs, queue and stats. Presets are shared: saving one
changes it for every instance using it.

### Borrow cards

Cards are matched by name, from the list in `data/borrow_cards.json`. To refresh it when
new cards come out:

```
.venv\Scripts\python.exe devtools/scrape_support_cards.py --images
```

You can also add a card by hand, and a refresh keeps it:

```json
{ "title": "Fire at My Heels", "character": "Kitasan Black", "image": "" }
```

### Dry runs

The Debug tab has switches that stop just before anything is spent:

- **Career Set-Up**: stops on Final Confirmation instead of pressing Start
- **Skill Buying**: selects skills but stops before Confirm
- **TP Refill**: opens Recover TP but stops before confirming
- **Waiting for TP**: pretends TP is short to test the wait (also `--pretend-tp-short`
  and `--tp-wait-seconds`)

## Known issues

- OCR can misread numbers. Stats are sanity-checked and recorded as blank if out of range,
  but skill points and fans have no such check.

## Help and contributing

- [FAQ](./readmes/FAQ.md) for common problems and how to report one
- Issues and contributions are welcome. See [BACKLOG.md](./BACKLOG.md) before starting work.

## Credits

Mirako Machine grew out of
[samsulpanjul/umamusume-auto-train](https://github.com/samsulpanjul/umamusume-auto-train)
and [CrazyIvanTR's fork](https://github.com/CrazyIvanTR/umamusume-auto-train). See
[CREDITS.md](./CREDITS.md) for details.
