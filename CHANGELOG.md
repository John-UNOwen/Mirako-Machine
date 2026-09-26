# Changelog

What changed, newest first. The bot reads this file from GitHub to show you what a newer
version contains, so the headings matter: one `## <version>` per release, and the version
comes first on the line.

Versions are read as "how much has changed", not as an API promise. Each release moves
the last number. The first two move only for a step that has been deliberately chosen
as one, so a change to either is worth reading about.

## 1.0.11 (2026-09-25)

- Rerolling the sparks without enough TP no longer stops the bot. The game asks whether
  to restore TP, and the bot used to not recognise that and give up as stuck. It now
  follows your TP refill settings: with refill on and the session's limit not reached,
  it restores TP and rerolls; otherwise it keeps the sparks as they are.
- The "Reroll the sparks?" question now lists "Bought Priority Skills" first, then
  "Matching Sparks" (the sparks that come from your priority skills, previously called
  Important skills). When the bot was restarted during the career it says so, since it
  cannot know what was bought before the restart.
- Discord notifications are sent as Mirako Machine instead of Tazuna (Uma-Auto).

## 1.0.10 (2026-09-25)

- The Discord messages about sparks now call the sparks that come from your priority
  skills "Important skills". The "Reroll the sparks?" question also gains a Priority line:
  the priority skills this career actually bought, out of how many are on the list.

## 1.0.9 (2026-09-25)

- Maximise Rating no longer considers a double circle the game does not have. Corner
  Adept, Corner Acceleration, Corner Recovery and Down in the Dirt stop at the single
  circle (their next step is a gold skill), but were being offered as a made-up double
  circle with no rating.
- A full Veteran Umamusume list is recognised. The bot used to close the popup as a login
  announcement and run into it again until it gave up and stopped. Now it tells you
  (Careers Paused), stops starting careers for the session and carries on with the daily
  tasks. Transfer some trainees in the game, then restart the bot to run careers again.
  With every daily task switched off it stops instead.

## 1.0.8 (2026-09-25)

- Spark reroll can ask first (Automation tab, Ask Before Rerolling). For a career a
  trigger allows, the Mirako bot sends the rating, the blue and pink sparks, the white
  sparks that match your priority skills, then every white spark with the total, and the
  sparks are rerolled only if you answer Reroll. Keep confirms them as granted and spends no TP.

## 1.0.7 (2026-09-25)

- The web page no longer goes blank when a preset predates a newer setting. That happened
  when the bot's files were updated while it kept running: the page came from the new
  files and the presets had not been brought up to date yet. Missing settings now take
  their defaults on the page.
- A skill whose price reading and hint badge disagree is no longer left unbought when
  the game itself offers it and the lower of the two prices fits the balance. The game
  is asked again before each purchase, so nothing is overspent.
- A career whose Training Log summary could not be read still gets its finish time, so
  its rating reaches run history.
- The spark question can no longer be mixed up with an earlier career's: each career's
  question is sent under its own key, and a question asked again after expiring gets a
  new one.
- An unexpected reply from the Mirako relay is reported as a relay problem instead of
  failing the spark step or the link and test buttons.
- The debug banner now lists "stops before rerolling sparks" when that switch is on.

## 1.0.6 (2026-09-25)

- The career rating now reaches run history and the Statistics tab. It was read, and
  shown in the Discord message, but the career had already been recorded without it.

## 1.0.5 (2026-09-25)

- Skill costs are double-checked against the hint badge (list price less its discount), so a
  price OCR misreads (for example 71 read as 7) is corrected to the exact price and the
  purchase plan no longer comes up short and drops skills at the end.
- Each career's rating is recorded: shown in the Statistics tab (per career and on average)
  and in the Discord career message.
- Spark reroll (Automation tab): after a career rated SS, or at any rating, the bot
  rerolls the sparks once (30 TP).
- Which set to keep is asked by Discord DM: both sets are sent as pictures, each with
  the white sparks it shares with your priority skills (an Uma Stan spark counts for
  Superstan), and you press a button. It comes from the Mirako bot: add it
  to your Discord account, run /link in its DMs, and paste the code into the Automation
  tab (Mirako Bot). No bot of your own is needed.
- Once the Mirako bot is linked, notifications come by DM too, in place of the webhook.

## 1.0.4 (2026-09-24)

- The bot restarts itself after an update or a rollback, other instances included, and
  the page reloads when it is back.
- New dependencies install after the old process has exited, so Windows no longer
  blocks replacing packages the bot had open.

## 1.0.3 (2026-09-24)

- Racing style buttons are found for every trainee, not only ones whose aptitude grades
  matched the reference capture.
- If a racing style button still can't be found, the bot stops after 3 tries instead of
  reopening the dialog forever.

## 1.0.2 (2026-09-24)

- Updated dependencies with known security issues (Pillow, Starlette, FastAPI and others).
- `start.bat` now installs updated dependencies by itself after an update.

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
