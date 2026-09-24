"""What counts as a preset, and what happens to everything else under config/.

From a checkup on 2026-09-20. The startup scan took "every .json under config/ except
presets.json and setup.json" to be a preset and ran the self-heal on each, which meant
any other file dropped in that directory was rewritten into a whole config. Two were:

  * `machine.json` -- the machine-wide overrides. `load_config` layers it on top of
    every instance, so injecting the template into it silently replaced every
    instance's own settings with template defaults, permanently and without a word.
  * `update_check.json` -- the update cache, added the same week. It grew from 208
    bytes to 19kB and showed up in the preset dropdown as a preset called "Preset",
    which a user could apply over their live config.

Also here: the seed preset must survive (every new preset is copied from it), one
unreadable file must not stop the process from starting, and two processes creating a
preset at the same moment must end with two presets rather than one overwriting the
other.

  py devtools/check_config_identity.py
"""
import io
import json
import multiprocessing
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as core_config                              # noqa: E402
import server.main as server                                   # noqa: E402
import update_config as uc                                     # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def naming_cases():
  print("What is a preset")
  for stem in ("default", "config_1", "config_17"):
    check(server.PRESET_STEM.match(stem) is not None, f"{stem}.json is a preset")
  for stem in ("machine", "update_check", "setup", "presets", "instances", "config_",
               "config_1_backup", "notes"):
    check(server.PRESET_STEM.match(stem) is None, f"{stem}.json is not")


def survivor_cases():
  """The two files the old rule ate, put through the same self-heal it applied."""
  print("\nWhat the old scan did to them, if it still reached them")
  with tempfile.TemporaryDirectory() as folder:
    shutil.copy("config.template.json", os.path.join(folder, "config.template.json"))
    os.makedirs(os.path.join(folder, "config"))
    here = os.getcwd()
    try:
      os.chdir(folder)
      machine = os.path.join("config", "machine.json")
      mine = {"sleep_time_multiplier": 2.0, "ocr_use_gpu": True}
      json.dump(mine, io.open(machine, "w", encoding="utf-8"), indent=2)
      uc.update_config(machine)
      healed = json.load(io.open(machine, encoding="utf-8"))
      # The property, not a count: this said `> 20` while the template had 53 keys, and
      # broke when the career settings were retired and it had 17. What the heal does to
      # a non-preset file is fill it with every template key a preset carries.
      template = json.load(io.open("config.template.json", encoding="utf-8"))
      stuffed = [k for k in template if k not in uc.SETUP_KEYS]
      check(stuffed and all(k in healed for k in stuffed)
            and not any(k in healed for k in mine),
            f"the self-heal would still strip and stuff it ({len(healed)} keys, "
            "user keys gone) -- which is why it must not be run on it")
    finally:
      os.chdir(here)

  check(server.PRESET_STEM.match("machine") is None,
        "so machine.json is kept out of the scan by name")
  check(server.PRESET_STEM.match("update_check") is None,
        "and so is the update cache")
  listed = [p.name for p in server.preset_files()]
  check("machine.json" not in listed and "update_check.json" not in listed,
        f"neither is listed as a preset: {listed}")


def seed_cases():
  print("\nThe seed preset")
  from fastapi import HTTPException
  try:
    server.remove_named_config("default")
    check(False, "deleting 'default' is refused")
  except HTTPException as refusal:
    check(refusal.status_code == 400 and "cannot be deleted" in refusal.detail,
          f"deleting 'default' is refused: {refusal.detail[:60]}")
  check(os.path.isfile(server.DEFAULT_CONFIG_PATH), "and the file is still there")


def _create_in_another_process(folder, done):
  """A second process creating a preset in the same directory, for the race below."""
  sys.path.insert(0, folder)
  import os as other_os
  path = other_os.path.join(folder, "config", "config_1.json")
  try:
    handle = other_os.open(path, other_os.O_CREAT | other_os.O_EXCL | other_os.O_WRONLY)
    other_os.close(handle)
    done.put("won")
  except FileExistsError:
    done.put("lost")


def race_cases():
  print("\nTwo creates at once")
  with tempfile.TemporaryDirectory() as folder:
    os.makedirs(os.path.join(folder, "config"))
    queue = multiprocessing.Queue()
    first = multiprocessing.Process(target=_create_in_another_process, args=(folder, queue))
    second = multiprocessing.Process(target=_create_in_another_process, args=(folder, queue))
    first.start(); second.start(); first.join(30); second.join(30)
    answers = sorted([queue.get(timeout=5), queue.get(timeout=5)])
    check(answers == ["lost", "won"],
          f"exclusive creation lets exactly one of two processes win: {answers}")

  source = io.open("server/main.py", encoding="utf-8").read()
  check("os.O_CREAT | os.O_EXCL | os.O_WRONLY" in source,
        "and the preset create uses it rather than an overwriting open")
  check("def get_next_config_id(taken=())" in source
        and 'Path(CONFIG_DIR).glob("config_*.json")' in source,
        "the next number is read off the disk, not off a list built once at import")


def tolerance_cases():
  print("\nOne unreadable file")
  source = io.open("server/main.py", encoding="utf-8").read()
  scan = source.split("#populate global config list once")[1][:700]
  check("except (OSError, ValueError)" in scan,
        "the startup scan skips a preset it cannot read instead of raising")
  check("continue" in scan and "[WARN] Skipping" in scan,
        "and says which file it skipped -- this runs at import, before anything names it")
  check("_warn_about_machine_config" in source,
        "and a machine.json carrying non-machine keys is reported at startup")


def ui_cases():
  """The two control-surface defects from the same checkup, asserted in the built bundle."""
  print("\nThe web UI")
  overview = io.open("web/src/components/OverviewSection.tsx", encoding="utf-8").read()
  # Every "waiting" branch, not one of them: the countdown cell and the Run now button
  # are two separate conditions, and only the countdown was gated on the local instance.
  waiting = overview.count('task.state === "waiting"')
  gated = overview.count('task.state === "waiting" && instance.local')
  check(waiting > 0 and waiting == gated,
        f"every waiting branch is gated on the local instance ({gated} of {waiting}) -- "
        "Run now drives this process's scheduler, and task names are the same on "
        "every instance")
  check("if (!res.ok)" in overview and "setControlError" in overview,
        "and a refused control write is shown rather than swallowed")

  app = io.open("web/src/App.tsx", encoding="utf-8").read()
  saver = app.split("persistPresetAndSetup")[1][:900]
  check("activeConfigId === appliedPresetId" in saver,
        "editing an unapplied preset no longer repoints the instance at it")

  bundle = io.open("web/dist/app.js", encoding="utf-8").read()
  check("Could not clear the hold" in bundle,
        "and the built bundle carries these -- web/dist is what the server serves")


def main():
  naming_cases()
  survivor_cases()
  seed_cases()
  race_cases()
  tolerance_cases()
  ui_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("config/ holds presets and other things, and only the presets are treated as one.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
