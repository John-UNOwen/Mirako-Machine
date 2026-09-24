"""Every setting in the config is one something reads.

From 2026-09-22. The turn-by-turn career was deleted across several commits in September
2026, about 105,000 lines of it, and not one of them touched the config schema. By
1.0.5, 35 of the template's 53 keys -- 58% of every config.json by size -- were settings
for that mode, and five things kept them there: the template carried them, the startup
heal only ever adds, the web UI's schema declared them required, reload_config
hard-indexed them so a file without them would not start, and main.py hid them from
the startup log instead of removing them. Its comment said "43 of the 66 config values
go unread".

Two of them had visible controls on the Setup page. "Enable Notification Sounds" was
loaded and never consulted, so unticking it changed nothing; "Info Sound" had nothing
that ever played it.

The checks here are the ones that would have caught it early:

  * a variable reload_config loads that nothing reads
  * a template key nothing loads or reads
  * a retired key coming back through the template, the schema or reload_config

plus the two behaviours that fix it for existing installs: the startup heal removes the
retired keys and nothing else, and the notifications box now switches the sound off.

  py devtools/check_config_readers.py
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import update_config as updater                                  # noqa: E402

failures = []

# Keys the web UI reads and the bot does not. Each one needs a reason.
UI_ONLY = {
  "theme": "the page's colour scheme; the bot has no appearance",
}


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def runtime_sources():
  """Every Python file the bot runs, as {path: text}. Not devtools, not the loader."""
  listed = subprocess.run(["git", "ls-files", "*.py"], capture_output=True,
                          text=True).stdout.split()
  return {path: io.open(path, encoding="utf-8", errors="ignore").read()
          for path in listed
          if not path.startswith("devtools/") and path != "core/config.py"}


def loader_cases():
  print("Every variable reload_config loads is read somewhere")
  loader = io.open("core/config.py", encoding="utf-8").read()
  loaded = re.findall(r"load_var\(\s*['\"](\w+)['\"]", loader)
  sources = runtime_sources()
  unread = []
  for name in loaded:
    reader = re.compile(r"(config\.|['\"])" + re.escape(name) + r"\b")
    if not any(reader.search(text) for text in sources.values()):
      unread.append(name)
  check(len(loaded) > 30, f"found the loader's variables ({len(loaded)})")
  check(not unread,
        f"none is loaded and then ignored -- {len(unread)} are: {unread[:8]}. A setting "
        "nobody reads looks exactly like one that works, from the page that sets it")


def template_cases():
  print("\nEvery template key is loaded or read")
  template = json.load(io.open("config.template.json", encoding="utf-8"))
  loader = io.open("core/config.py", encoding="utf-8").read()
  sources = runtime_sources()
  unclaimed = []
  for key in template:
    quoted = re.compile(r"['\"]" + re.escape(key) + r"['\"]")
    if key in UI_ONLY:
      continue
    if quoted.search(loader) or any(quoted.search(text) for text in sources.values()):
      continue
    unclaimed.append(key)
  check(not unclaimed,
        f"no key sits in every config with nothing behind it: {unclaimed}. If the page "
        "alone reads it, add it to UI_ONLY with the reason")


def retired_cases():
  print("\nThe retired settings stay retired")
  template = json.load(io.open("config.template.json", encoding="utf-8"))
  schema = io.open("web/src/types/index.ts", encoding="utf-8").read()
  skill_schema = io.open("web/src/types/skill.type.ts", encoding="utf-8").read()
  loader = io.open("core/config.py", encoding="utf-8").read()

  back = [key for key in updater.RETIRED_KEYS if key in template]
  check(not back, f"none is in the template, which would put it back in every file: {back}")
  declared = [key for key in updater.RETIRED_KEYS
              if re.search(r"^\s+" + re.escape(key) + r":", schema, re.M)]
  check(not declared, f"none is declared by the web UI's schema: {declared}")
  indexed = [key for key in updater.RETIRED_KEYS
             if re.search(r"config\[['\"]" + re.escape(key) + r"['\"]\]", loader)]
  check(not indexed,
        f"none is hard-indexed by reload_config, which made a file without them refuse "
        f"to start: {indexed}")
  for block, subkeys in updater.RETIRED_NESTED.items():
    kept = [sub for sub in subkeys if sub in template.get(block, {}) or sub in skill_schema]
    check(not kept, f"and none of {block}'s retired sub-keys survives: {kept}")

  app = io.open("web/src/App.tsx", encoding="utf-8").read()
  # Accesses through config, not the word: the guard's own comment says what it used to
  # test, and a substring check fails on the explanation.
  check(not re.search(r"config\??\.(event|race_schedule)\b", app),
        "the page no longer waits on, or counts, a career block -- its Loading guard "
        "tested event.event_choices, so retiring the block alone would have hung it")
  bundle = io.open("web/dist/app.js", encoding="utf-8").read()
  check("event_choices" not in bundle and "training_strategy" not in bundle,
        "and the built bundle agrees -- web/dist is what the server serves")


# The last template before the retirement, from this branch's own history -- so the case
# below heals a real 1.0.5 config rather than a fixture shaped like one.
LAST_CAREER_TEMPLATE = "47162c2"


def heal_cases():
  print("\nAn existing file loses the retired settings, and only those")
  shown = subprocess.run(["git", "show", f"{LAST_CAREER_TEMPLATE}:config.template.json"],
                         capture_output=True, text=True, encoding="utf-8")
  check(shown.returncode == 0, f"the 1.0.5 template is readable at {LAST_CAREER_TEMPLATE}")
  if shown.returncode != 0:
    return
  with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "preset.json")
    old = json.loads(shown.stdout)
    check(all(key in old for key in updater.RETIRED_KEYS),
          "which really does carry every retired key, so the case is real")
    old["skill"]["skill_list"] = ["Mine, not the template's"]
    old["something_i_added"] = "by hand"          # the merge keeps what it does not know
    io.open(path, "w", encoding="utf-8").write(json.dumps(old))

    updater.update_config(path)
    healed = json.load(io.open(path, encoding="utf-8"))

    left = [key for key in updater.RETIRED_KEYS if key in healed]
    check(not left, f"every retired key is gone: {left}")
    check(not any(sub in healed["skill"] for sub in updater.RETIRED_NESTED["skill"]),
          "and so are the skill block's three")
    check(healed["skill"]["skill_list"] == ["Mine, not the template's"],
          "while the skill list in the same block is untouched")
    check(healed.get("something_i_added") == "by hand",
          "and a key the user added is kept -- this is a named list, not a prune of "
          "everything the template lacks, which would also delete a newer build's keys "
          "after a rollback")

    size_before = len(json.dumps(old))
    check(len(json.dumps(healed)) < size_before / 2,
          f"the file is most of the way smaller ({size_before} -> {len(json.dumps(healed))} "
          "bytes), which is the point")

    updater.update_config(path)
    check(json.load(io.open(path, encoding="utf-8")) == healed,
          "and a second start changes nothing: nothing grows back")


def notification_cases():
  print("\nThe notifications box switches the sound off")
  import core.config as config
  import scenarios.independent_recovery as recovery
  from utils.notifications import StopReason

  played = []
  saved = (recovery.device_action.stop_bot,
           getattr(config, "NOTIFICATIONS_ENABLED", None),
           getattr(config, "SUCCESS_NOTIFICATION", None))
  recovery.device_action.stop_bot = lambda reason, sound, volume=0.3: played.append(sound)
  config.SUCCESS_NOTIFICATION = "done.mp3"
  try:
    config.NOTIFICATIONS_ENABLED = True
    recovery._stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION", "finished")
    check(played[-1] == "assets/notifications/done.mp3", f"ticked, it plays: {played[-1]}")

    config.NOTIFICATIONS_ENABLED = False
    recovery._stop(StopReason.FINISHED, "SUCCESS_NOTIFICATION", "finished")
    check(played[-1] is None,
          f"unticked, it does not -- it used to play regardless, got {played[-1]}")
    check(len(played) == 2, "and the stop itself still happens either way")
  finally:
    (recovery.device_action.stop_bot, config.NOTIFICATIONS_ENABLED,
     config.SUCCESS_NOTIFICATION) = saved


def main():
  loader_cases()
  template_cases()
  retired_cases()
  heal_cases()
  notification_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Every setting is one something reads.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
