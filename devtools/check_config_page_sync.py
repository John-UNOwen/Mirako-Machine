"""An edit on the config page stays made, on the page and on the server.

Reported 2026-09-23: adding or removing a prioritised skill "took several tries". Reproduced
in a browser by removing one skill and polling both sides every 250ms:

     0ms  page shows it: YES   server has it: YES
   251ms  page shows it: no    server has it: YES     (the auto-save's 900ms wait)
  1037ms  page shows it: YES   server has it: no      (saved -- and put back on the page)

and it stayed that way. The page rebuilt its workspace from its copy of the preset whenever
`presets`, `activeConfig` or `setupConfig` changed, and a save changes all of them -- but
that copy was only ever refreshed by fetching, and nothing fetched after a save. So the
page threw the edit away a second after making it, and the next edit posted the page's
stale copy and undid the first one on the server too. It "stuck" only when the window
next took focus, which refetches.

Fixed in two halves, and a live test of each half is below:

  * A save of the page's own edits becomes the page's copy of the preset (acceptSaved).
  * The workspace is rebuilt only when something is *loaded* -- a preset fetched or
    switched to, or the Setup values arriving -- counted by activeConfigLoads and
    setupLoads, never on a save.

The second half is for edits made while a save is in flight. With saves slowed to 700ms,
as a server sharing its process with the bot's OCR is: with acceptSaved alone, a skill
removed mid-save came back and a skill added mid-save never arrived. With both halves all
three edits landed. That run was done in the browser, where this repo has no test runner;
what can be pinned offline is the shape, which is what this suite holds.

  py devtools/check_config_page_sync.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def read(path):
  return io.open(path, encoding="utf-8").read()


def rebuild_effect(app):
  """The body and dependency list of the effect that rebuilds the workspace."""
  found = re.search(r"useEffect\(\(\) => \{\s*if \(presets\[activeIndex\]\) \{\s*"
                    r"setConfig\(mergeConfigWithSetup\(.*?\}, \[(.*?)\]\);", app, re.S)
  return found.group(0) if found else "", found.group(1) if found else ""


def rebuild_cases():
  print("The workspace is rebuilt on a load, not on a save")
  app = read("web/src/App.tsx")
  effect, deps = rebuild_effect(app)
  check(bool(effect), "the rebuild effect is where it was")
  named = {d.strip() for d in deps.split(",") if d.strip()}
  check({"activeConfigLoads", "setupLoads"} <= named,
        f"it runs when a preset or the Setup values are loaded: [{deps}]")
  check(not named & {"presets", "activeConfig", "setupConfig", "activeIndex"},
        "and not when presets, activeConfig, setupConfig or activeIndex change -- a save "
        "changes those, and rebuilding on them threw the edit away")

  hook = read("web/src/hooks/useConfigPreset.ts")
  fetch_effect = hook.split("void getConfigFromServer(activeConfigId)")[1][:300]
  check("setActiveConfigLoads" in fetch_effect,
        "a fetch landing counts as a load")
  check(hook.count("setActiveConfigLoads((n) => n + 1)") >= 2,
        "and so does the preset being cleared, so the page never shows a gone preset")
  check("setSetupLoads((n) => n + 1)" in app.split("const getSetupConfig")[1][:600],
        "and the Setup values arriving counts too, since they are merged into the page")


def save_cases():
  print("\nA save of the page's own edits becomes its baseline")
  app = read("web/src/App.tsx")
  persist = app.split("const persistPresetAndSetup")[1].split("}, [")[0]
  check("acceptSaved(activeConfigId, configWithoutSetup)" in persist,
        "persistPresetAndSetup hands what it saved to acceptSaved")
  check("acceptSaved(" in persist and "await savePreset(" in persist
        and persist.index("await savePreset(") < persist.index("acceptSaved("),
        "only once the save has succeeded -- a failed save is not the new baseline")

  hook = read("web/src/hooks/useConfigPreset.ts")
  accept = hook.split("const acceptSaved")[1][:400]
  check("prev.id === presetId" in accept,
        "and it only ever updates the preset that was saved, not whichever is showing")
  check("setActiveConfigLoads" not in accept,
        "without counting as a load -- that would rebuild the page on every save again")


def import_cases():
  print("\nAn import shows what was imported")
  hook = read("web/src/hooks/useImportConfig.ts")
  body = hook.split("await savePresetById(createdPreset.id, config);")
  check(len(body) == 2 and "reloadActiveConfig()" in body[1][:600],
        "it reloads after saving: the page switched to the new preset before the save, "
        "so the page's copy is the seed, and taking the import as saved would have "
        "auto-saved that seed over it")


def comparison_cases():
  """Reported the same day, after the fix above: "Saving changes" stayed up after an edit
  until the page was refreshed -- the page certain it had something unsaved, and the
  loop guard refusing to save the same bytes twice. It was not reproduced; what was found
  instead is that the comparison behind it treated key order as content, which the fix
  above had made reachable (a save no longer rebuilds the page into the baseline's order),
  and that the server was sending the page a key 1.0.6 had retired, in its own position."""
  print("\nWhether anything is unsaved is a question about content, not key order")
  app = read("web/src/App.tsx")
  check("canonical(config) !== canonical(baselineConfig)" in app,
        "isDirty compares the two configs canonically")
  check("const snapshot = canonical(config);" in app,
        "and so does the loop guard, which has to agree with it about what was saved")
  helper = app.split("const canonical =")[1][:500] if "const canonical =" in app else ""
  check(".sort(" in helper and "Array.isArray" in helper,
        "sorting object keys but not arrays -- the skill list's order is its priority")


def save_state_cases():
  """From the review of 8f5ae63 (2026-09-23). saveConfig was a new function every render,
  so the auto-save effect ran after every render: "Saved" was cleared in the frame it was
  set, and a failed save went back to idle -- which, with the edit still unsaved, drew the
  "Saving changes" spinner for as long as the save went on failing, with nothing on the
  page to say it was failing."""
  print("\nThe save pill says what happened, and a failed save is tried again")
  hook = read("web/src/hooks/useConfig.ts")
  check("const saveConfig = useCallback(" in hook,
        "saveConfig is stable across renders, so the auto-save effect runs on edits only")
  app = read("web/src/App.tsx")
  autosave = app.split("const autoSave = useCallback(")[1].split("}, [")[0]
  check('setSaveState("failed")' in autosave and 'setSaveState("idle")' not in autosave,
        "a failed save is recorded as failed, not as idle")
  check("RETRY_FAILED_SAVE_MS" in app and 'saveState !== "failed" || !isDirty' in app,
        "and retried on its own, rather than waiting for another edit")
  retry = app.split('saveState !== "failed" || !isDirty')[1].split("]);")[0]
  check("saveFailures" in retry and "setSaveFailures((n) => n + 1)" in autosave,
        "every failure schedules the next retry -- a quick one went failed, saving, failed "
        "in one batch, which is no change, and the second failure retried nothing (found "
        "live, with the page's saves forced to fail)")
  check("SAVED_SHOWN_MS" in app, '"Saved" stays up for a moment before it goes')
  check("Not saved. Trying again shortly." in app, "the pill says when a save has failed")
  check('saveState === "saving" || saveState === "failed"' in app,
        "and never starts a second save while one is in flight")


def load_gate_cases():
  """The page renders from the template before its loads land, and an edit in that window
  auto-saved the template's Setup values over the instance's real ones."""
  print("\nNothing is saved before the page holds what the server holds")
  app = read("web/src/App.tsx")
  loaded = app.split("const loaded =")[1].split(";")[0] if "const loaded =" in app else ""
  check("presetsReady" in loaded and "setupLoads > 0" in loaded
        and "activeConfig?.id === activeConfigId" in loaded,
        "loaded needs the preset list, the Setup values and the chosen preset")
  autosave = app.split("const autoSave = useCallback(")[1].split("}, [")[0]
  check("if (!loaded) return true;" in autosave,
        "auto-save does nothing until then -- including the save a preset switch asks for")
  hook = read("web/src/hooks/useConfigPreset.ts")
  check("setPresetsReady(true)" in hook.split("finally")[1][:120] if "finally" in hook else False,
        "the preset list counts as answered even when it fails, so a dead server cannot "
        "leave the page never saving setup edits")


def small_page_cases():
  print("\nThe smaller page fixes")
  app = read("web/src/App.tsx")
  theme = app.split("--secondary")[1][:200]
  check('updateConfig("theme"' not in theme,
        "a theme this install does not have is shown as the first one, not saved over")
  apply = app.split("const handleApplyPreset")[1].split("}, [")[0]
  check("notify(" in apply, "a failed Apply is announced, not only logged")
  banner = read("web/src/components/InstanceBanner.tsx")
  switch = banner.split("const switchTo = useCallback(")[1].split("[beforeSwitch, tab, dark]")[0]
  check("finally" not in switch and "window.confirm(" in switch,
        "switching instances after a failed save asks before losing the edit")
  overview = read("web/src/components/OverviewSection.tsx")
  run_now = overview.split("const runNow")[1].split("finally")[0]
  check("body?.detail" in run_now, "Run now shows the server's reason, not just the status")
  check("exportOldConfigs" not in app and not os.path.exists("migrate_local_storage_presets.py"),
        "the broken old-preset migration is gone, button and script both")
  check(not os.path.exists("web/src/constants/index.ts"),
        "and so are the career mode's dead UI constants")


def retired_setup_cases():
  print("\nThe server stops serving a retired setting")
  import io as _io
  import json
  import shutil
  import tempfile
  sys.argv = sys.argv[:1]
  import core.bot as bot
  import core.config as core_config
  import server.main as server
  from update_config import RETIRED_KEYS
  folder = tempfile.mkdtemp(prefix="check_page_sync_")
  saved = (server.GLOBAL_SETUP_PATH, core_config.MACHINE_PATH, core_config.CONFIG_PATH,
           bot.instance_name)
  try:
    server.GLOBAL_SETUP_PATH = os.path.join(folder, "setup.json")
    core_config.MACHINE_PATH = os.path.join(folder, "machine.json")
    core_config.CONFIG_PATH = os.path.join(folder, "config.json")
    bot.instance_name = ""
    _io.open(core_config.CONFIG_PATH, "w", encoding="utf-8").write("{}")
    _io.open(server.GLOBAL_SETUP_PATH, "w", encoding="utf-8").write(json.dumps(
        {"use_adb": True, "info_notification": "sfx_01.mp3", "device_id": "127.0.0.1:5555"}))
    served = server.get_setup_config()
    check("info_notification" not in served and served.get("use_adb") is True,
          f"GET /config/setup leaves out what 1.0.6 retired, and keeps the rest: {served}")
    server.update_setup_config({"use_adb": False})
    written = json.loads(_io.open(server.GLOBAL_SETUP_PATH, encoding="utf-8").read())
    check("info_notification" not in written and written.get("use_adb") is False,
          f"and the next Setup save drops it from setup.json for good: {written}")
    check(all(key not in written for key in RETIRED_KEYS),
          "along with anything else retired")
  finally:
    (server.GLOBAL_SETUP_PATH, core_config.MACHINE_PATH, core_config.CONFIG_PATH,
     bot.instance_name) = saved
    shutil.rmtree(folder, ignore_errors=True)


def main():
  rebuild_cases()
  save_cases()
  import_cases()
  comparison_cases()
  save_state_cases()
  load_gate_cases()
  small_page_cases()
  retired_setup_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("What the page shows is what was saved, and an edit mid-save is kept.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
