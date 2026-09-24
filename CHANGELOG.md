# Changelog

What changed, newest first. The bot reads this file from GitHub to show you what a newer
version contains, so the headings matter: one `## <version>` per release, and the version
comes first on the line.

Versions are read as "how much has changed", not as an API promise. Each release moves
the last number. The first two move only for a step that has been deliberately chosen
as one, so a change to either is worth reading about.

## 1.0.1 (2026-09-24)

- **Fixed: the update banner could announce a version that does not exist.** The bot
  keeps GitHub's answer for six hours, and it reused an answer saved by a different
  version, for example one that came along with a copied `config` folder. It now asks
  again whenever the version it is running has changed.

## 1.0.0 (2026-09-23)

Mirako Machine starts counting again, with a single commit as its history.

What this build does:

- **Independent Training, end to end.** It drives the set-up screens, waits the career
  out, spends the skill points, drives the teardown and starts the next one, as many
  times as you ask.
- **A task queue** rather than a career loop: daily races, Team Trials, mission and
  present collection and the Daily Sale all run on their own schedules around the
  careers. Mission rewards and presents are collected again whenever their icon shows
  something new.
- **Several emulators at once** from one web UI, each with its own settings and its own
  schedule.
- **Race agendas, Training Focus and Racing Style** chosen for you on the confirmation
  screen.
- **TP recovery** under a strategy you choose, with a carat floor and a cap.
- **Recovery on its own**: reconnects, restarts the game when it is stuck, and stops with
  a clear reason when it cannot go on.
- **Updating from a button**, which checks what could go wrong before it starts, and
  going back to the previous version if a release turns out worse.

**If you installed an earlier version, re-clone rather than update.** The history is not
shared with the old one, so neither the Update button nor `git pull` can cross from one
to the other. Your settings and stats are not part of the repository: copy `config.json`
and the `config` and `stats` folders into the new clone to keep them.
