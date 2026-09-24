"""A JSON file is written whole, by whichever thread gets there.

From a review of the 1.1.9 -> 1.2.0 fixes. The commits that introduced `os.replace` for
the config and schedule writes fixed the torn read but left a second hole underneath it:
every writer built its temporary name from the destination, so two of them shared it.

  * `server/main.py` and `core/scheduler.py` both write `schedule.json`, through
    `schedule.json.tmp`. They do not share a thread -- one runs on a uvicorn request, the
    other on the bot -- so they can interleave, and one `os.replace` moves a file the
    other is still filling. The write lands torn despite being "atomic", which is the
    "Clear does nothing / run-now 404s" symptom the fix was for.
  * A PID in the name is not enough, because both of those are in one process.
  * `update_config.py` and `create_preset_file` were not atomic at all. The first runs at
    startup against the live config, so a kill mid-write left an instance that would not
    boot; the second left a truncated preset, which the startup scan skips -- so it
    vanished from the dropdown with nothing said.

Also here: applying a preset must write the pointer. The guard that stopped auto-save
repointing the instance also stopped Apply from ever writing it, and the only endpoint
was a GET.

  py devtools/check_atomic_writes.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.atomic_write import (REPLACE_DEADLINE, read_json_retrying,
                               write_json_atomic)                  # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def wholeness_cases():
  print("A write is whole or absent")
  with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "thing.json")
    write_json_atomic(path, {"a": 1})
    check(json.load(io.open(path, encoding="utf-8")) == {"a": 1}, "a plain write lands")

    write_json_atomic(path, {"a": 2})
    check(json.load(io.open(path, encoding="utf-8")) == {"a": 2}, "and a rewrite replaces it")

    # Something unserialisable, discovered halfway through json.dump.
    try:
      write_json_atomic(path, {"ok": 1, "bad": object()})
    except TypeError:
      pass
    check(json.load(io.open(path, encoding="utf-8")) == {"a": 2},
          "a write that fails partway leaves the previous file untouched")
    leftovers = [n for n in os.listdir(folder) if n != "thing.json"]
    check(not leftovers, f"and cleans up after itself, found {leftovers}")

    deep = os.path.join(folder, "a", "b", "c.json")
    write_json_atomic(deep, {"made": True})
    check(os.path.isfile(deep), "a missing directory is created rather than raising")


def concurrency_cases():
  """Many threads onto one path. No reader may ever see anything but a whole document."""
  print("\nTwo threads writing one file")
  with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "schedule.json")
    write_json_atomic(path, {"writer": "seed", "payload": []})
    bad = []
    stop = threading.Event()

    def writer(name):
      # Big enough that a torn write is torn visibly rather than landing in one block.
      document = {"writer": name, "payload": [name] * 4000}
      for _ in range(60):
        write_json_atomic(path, document)

    def reader():
      # Through the helper, as the server's own reads are: on Windows a read arriving
      # during a swap is denied outright, so a reader that does not retry reports a file
      # it could not read -- which every caller here turns into an empty config.
      while not stop.is_set():
        loaded = read_json_retrying(path)
        if loaded is None:
          bad.append("a read came back empty while the file existed throughout")
          continue
        if set(loaded.get("payload") or [loaded["writer"]]) != {loaded["writer"]}:
          bad.append("mixed content from two writers in one file")
        time.sleep(0.001)

    watcher = threading.Thread(target=reader)
    watcher.start()
    writers = [threading.Thread(target=writer, args=(n,)) for n in ("left", "right", "third")]
    for thread in writers:
      thread.start()
    for thread in writers:
      thread.join(60)
    stop.set()
    watcher.join(10)

    check(not bad, f"no reader saw a torn or mixed file, got {len(bad)}: {bad[:2]}")
    final = json.load(io.open(path, encoding="utf-8"))
    check(final["writer"] in ("left", "right", "third"),
          "and the file that survives is one writer's, whole")
    leftovers = [n for n in os.listdir(folder) if n != "schedule.json"]
    check(not leftovers, f"with no temporary files stranded, found {leftovers[:3]}")


def held_open_cases():
  """A reader with the file open must not be able to fail the write."""
  print("\nA reader holding the file open")
  with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "held.json")
    write_json_atomic(path, {"round": 0})

    held = io.open(path, encoding="utf-8")
    try:
      # Let go from another thread, as a real reader does the moment its read is done.
      timer = threading.Timer(0.15, held.close)
      timer.start()
      write_json_atomic(path, {"round": 1})
      timer.cancel()
      check(json.load(io.open(path, encoding="utf-8")) == {"round": 1},
            "the write waits for the handle to go and then lands")
    finally:
      try:
        held.close()
      except OSError:
        pass

  check(REPLACE_DEADLINE >= 1.0,
        f"and waits long enough to be worth waiting, {REPLACE_DEADLINE}s")


def call_site_cases():
  """The four writers that had the bug, asserted at their source."""
  print("\nEvery writer goes through it")
  for path, what in (("server/main.py", "the server's config and schedule writes"),
                     ("core/scheduler.py", "the scheduler's cooldown file"),
                     ("core/instances.py", "a new instance's settings"),
                     ("update_config.py", "the startup self-heal")):
    source = io.open(path, encoding="utf-8").read()
    check("write_json_atomic" in source, f"{path} -- {what}")
    check('.tmp"' not in source and ".tmp'" not in source,
          f"{path} no longer builds a temporary name from the destination")

  helper = io.open("core/atomic_write.py", encoding="utf-8").read()
  check("mkstemp" in helper,
        "and the name comes from mkstemp, which is unique per call -- a PID is not "
        "enough, because the two writers that collided share a process")
  check("dir=folder" in helper,
        "made in the destination's own directory, since os.replace is only atomic "
        "within one filesystem")


def applied_preset_cases():
  print("\nApplying a preset writes the pointer")
  source = io.open("server/main.py", encoding="utf-8").read()
  check('@app.post("/config/applied-preset")' in source,
        "there is a POST route at all -- there used to be only the GET")
  # The whole route, up to the next one. A fixed 1200 characters stopped matching the day
  # the route gained a guard ahead of its write.
  route = source.split('@app.post("/config/applied-preset")')[1].split("\n@app.")[0]
  check('own["preset_id"] = preset_id' in route and "_write_json_object" in route,
        "and it writes preset_id to the instance's own config")
  check("_apply_saved_config()" in route,
        "then reloads, so the running bot follows the preset it was just given")
  check("HTTPException" in route,
        "a preset that does not exist is refused rather than written")

  hook = io.open("web/src/hooks/useConfigPreset.ts", encoding="utf-8").read()
  setter = hook.split("const setAppliedPresetId")[1][:500]
  check('method: "POST"' in setter,
        "the UI sends a POST -- it used to GET the endpoint and only set local state, "
        "so Apply changed nothing on disk and a refresh undid it")
  check("preset_id: presetId" in setter, "carrying the preset it is applying")

  bundle = io.open("web/dist/app.js", encoding="utf-8").read()
  check("/config/applied-preset" in bundle and "preset_id" in bundle,
        "and the built bundle carries it -- web/dist is what the server serves")


def main():
  wholeness_cases()
  concurrency_cases()
  held_open_cases()
  call_site_cases()
  applied_preset_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Whole writes, unique temporaries, and an Apply that sticks.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
