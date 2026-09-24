"""Careers are recorded per device, and read back as one history.

Phase 5d. `stats/runs.jsonl` and `stats/pending.json` were the last writers keyed on
nothing: two instances appending careers from different accounts to one file, and doing
read-modify-write on one tally. They now sit beside `schedule.json` under the device key
the scheduler has always used.

**Nothing is migrated, and that is the decision rather than an omission.** Moving the old
history would have to choose which device it belonged to, and the file cannot say -- the
only honest answer is "whichever emulator was running", which is not in it. So the legacy
file is read forever and written never, and an upgrade keeps every career it had.

**One history on the way out.** There is one person behind however many emulators, so the
Stats page aggregates. Records carry `device` from this change onwards, which is what a
per-instance view would filter on later; the ones without it are the ones from before
there was a question.

The pending tally is deliberately *not* carried over: it holds refill timestamps with a
two-hour ceiling, so the entire cost of leaving it is one career undercounting refills
once, against a migration that would need the same unanswerable guess.

Run against temporary directories; it never touches the real stats.

One warning for whoever mutation-tests this next: several of the mutations worth making
here are precisely the ones that stop an explicit path being honoured, and under those the
cases below write their fixtures into the *real* `stats/` instead of a temporary one. That
is the mutation being caught rather than a fault in the case, but it leaves `{"fans": 1}`
records behind. Check `stats/` afterwards and delete what does not belong.

  py devtools/check_stats_scoping.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.independent_stats as stats                            # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def as_device(device_id):
  bot.use_adb, bot.device_id = True, device_id


def write_runs(path, records):
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with io.open(path, "w", encoding="utf-8") as handle:
    for record in records:
      handle.write(json.dumps(record) + "\n")


def path_cases():
  print("\nWhere a device writes:")
  saved = (bot.use_adb, bot.device_id)
  try:
    as_device("127.0.0.1:5555")
    first = stats.runs_path()
    check(os.path.basename(os.path.dirname(first)) == "127.0.0.1_5555",
          f"the path carries the device, readably: {first}")
    # Resolved per call. These used to be defaults bound in the signature, which are
    # evaluated at import -- before the device is read from the config or --use-adb, so
    # every instance would have written wherever the importer happened to point.
    as_device("127.0.0.1:5565")
    check(stats.runs_path() != first,
          "a second emulator writes somewhere else, so nothing was bound at import")
    check(os.path.dirname(stats.runs_path()) == os.path.dirname(stats.pending_path()),
          "and its tally sits beside its history")
    bot.use_adb = False
    check("desktop" in stats.runs_path(),
          f"the desktop path takes the literal key, got {stats.runs_path()}")
  finally:
    bot.use_adb, bot.device_id = saved


def separation_cases():
  print("\nTwo devices do not share a file:")
  saved = (bot.use_adb, bot.device_id)
  tmp = tempfile.mkdtemp(prefix="check_stats_")
  try:
    as_device("10.0.0.1:5555")
    left = stats.runs_path(tmp)
    stats.record_run({"fans": 1}, left)
    as_device("10.0.0.2:5555")
    right = stats.runs_path(tmp)
    stats.record_run({"fans": 2}, right)

    check(left != right and os.path.isfile(left) and os.path.isfile(right),
          "each device got its own file")
    check([r["fans"] for r in stats.read_runs(left)] == [1],
          f"and only its own careers: {stats.read_runs(left)}")
    # The stamp is what makes an aggregated history separable again.
    check(stats.read_runs(left)[0].get("device") == "10.0.0.1_5555",
          f"records say which device they came from: {stats.read_runs(left)[0]}")
    check(stats.read_runs(right)[0].get("device") == "10.0.0.2_5555",
          "and the other device's say the other thing")
  finally:
    bot.use_adb, bot.device_id = saved
    shutil.rmtree(tmp, ignore_errors=True)


def aggregate_cases():
  print("\nRead back as one history:")
  saved = (bot.use_adb, bot.device_id)
  tmp = tempfile.mkdtemp(prefix="check_stats_")
  try:
    # A history from before the split, plus two devices since.
    write_runs(os.path.join(tmp, "runs.jsonl"), [{"finished_at": "2026-01-01T00:00:00"}])
    # The newer career deliberately sits in the alphabetically *earlier* directory, so
    # reading the files in order gives the wrong answer and only the sort gives the right
    # one. Laid out the other way round first, where the natural order happened to be
    # chronological and dropping the sort changed nothing.
    write_runs(os.path.join(tmp, "10.0.0.1_5555", "runs.jsonl"),
               [{"finished_at": "2026-03-01T00:00:00", "device": "10.0.0.1_5555"}])
    write_runs(os.path.join(tmp, "10.0.0.2_5555", "runs.jsonl"),
               [{"finished_at": "2026-02-01T00:00:00", "device": "10.0.0.2_5555"}])

    found = stats.history_paths(tmp)
    check(len(found) == 3, f"every file is found, got {len(found)}")
    check(found[0] == os.path.join(tmp, "runs.jsonl"),
          "the pre-split history is read rather than stranded -- an upgrade keeps it")

    runs = stats.read_all_runs(tmp)
    check(len(runs) == 3, f"all three careers come back, got {len(runs)}")
    check([r["finished_at"][:7] for r in runs] == ["2026-01", "2026-02", "2026-03"],
          f"oldest first across devices, got {[r['finished_at'][:7] for r in runs]}")
    check(runs[0].get("device") is None,
          "the pre-split record carries no device, which is what dates it")

    check(stats.clear_all_runs(tmp), "clearing reports success")
    check(stats.read_all_runs(tmp) == [], "and the history is empty afterwards")
    check(all(os.path.isfile(path + ".bak") for path in found),
          "with every file backed up first -- this is a button in a web page")
  finally:
    bot.use_adb, bot.device_id = saved
    shutil.rmtree(tmp, ignore_errors=True)


def isolation_cases():
  """An explicit path must stay explicit, or a test writes the user's real stats."""
  print("\nAn explicit path is honoured:")
  tmp = tempfile.mkdtemp(prefix="check_stats_")
  try:
    tally = os.path.join(tmp, "pending.json")
    stats.add_pending_refill(tally, now=1000.0)
    check(os.path.isfile(tally), "the tally went where it was told, not to the device")
    check(stats.take_pending_refills(tally, now=1001.0) == 1,
          "and reads back from there")
    check(not os.path.exists(stats.pending_path(tmp)),
          "with nothing written to the device path that was never asked for")
  finally:
    shutil.rmtree(tmp, ignore_errors=True)


def server_cases():
  print("\nThe page reads the whole history:")
  source = io.open("server/main.py", encoding="utf-8").read()
  check("read_all_runs()" in source and "import read_runs" not in source,
        "/stats/runs aggregates rather than showing one device's careers")
  check("clear_all_runs()" in source,
        "and the reset button clears what the page was showing")


def main():
  path_cases()
  separation_cases()
  aggregate_cases()
  isolation_cases()
  server_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("Each device keeps its own careers, and the page still shows one history.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
