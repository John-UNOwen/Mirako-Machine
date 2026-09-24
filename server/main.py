from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import os
import json
import re
import time

from update_config import RETIRED_KEYS, SETUP_KEYS
from update_config import update_config as _update_config

import core.bot as bot
import core.config as core_config
from core.atomic_write import read_json_retrying, write_json_atomic
import core.updates as updates
import core.version as core_version
from core.device_claim import owner_of
from core.scheduler import FILE_LOCK as SCHEDULE_FILE_LOCK, device_key, entered_task, schedule_path
app = FastAPI()

# The version this process started on, read once at startup. After an in-place update the
# files on disk say a newer number, but this process is still running the old code -- and
# a stale process announcing itself as the new version makes the "update available" banner
# vanish exactly when it matters most. Everything that reports "what are you on" (the
# /version.txt route, /update/status) answers from this stamp instead of re-reading
# version.txt per request. A restart picks up the new number.
BOOT_VERSION = core_version.current()

# main.py fills these in at startup. The wiring goes this way round because main.py
# imports `app` from here, so this module cannot import it back. Until they are
# registered every control endpoint reports itself unavailable rather than pretending.
BOT_CONTROL = {}


def register_bot_control(**handlers):
  """Called once by main.py with start / stop / stop_after_career callables."""
  BOT_CONTROL.update(handlers)


def _control(name):
  handler = BOT_CONTROL.get(name)
  if handler is None:
    raise HTTPException(status_code=503,
                        detail="The bot's controls are not registered yet.")
  return handler

# resolved base dirs
# The default only. Every read goes through core_config.config_path(), which
# answers for the instance this process actually is.
CONFIG_PATH = "config.json"
CONFIG_TEMPLATE_PATH = "config.template.json"
CONFIG_DIR = "config"
GLOBAL_SETUP_PATH = f"{CONFIG_DIR}/setup.json"
DEFAULT_CONFIG_PATH = f"{CONFIG_DIR}/default.json"
THEMES_DIR = "themes/"
DATA_DIR = "data/"
WEB_DIR = "web/dist/"

# startup actions
setup_json_exists = os.path.exists(GLOBAL_SETUP_PATH)
default_json_exists = os.path.exists(DEFAULT_CONFIG_PATH)
if not setup_json_exists or not default_json_exists:
  with open(CONFIG_TEMPLATE_PATH, "r", encoding="utf-8") as template_file:
    template = json.load(template_file)
    if not setup_json_exists:
      setup_template = {k: v for k, v in template.items() if k in SETUP_KEYS}
      with open(GLOBAL_SETUP_PATH, "w+", encoding="utf-8") as setup_file:
        json.dump(setup_template, setup_file, indent=2)
    if not default_json_exists:
      default_template = {k: v for k, v in template.items() if k not in SETUP_KEYS}
      with open(DEFAULT_CONFIG_PATH, "w+", encoding="utf-8") as default_config_file:
        json.dump(default_template, default_config_file, indent=2)

# restrict CORS to localhost
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")
app.add_middleware(
  CORSMiddleware,
  allow_origin_regex=LOCAL_ORIGIN.pattern,
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

# CORS only stops a foreign page *reading* a response. A body-less POST is a "simple"
# request, which a browser sends without asking first, so any site open in any tab could
# still start or stop the bot, clear holds or reset stats -- it just never saw the answer.
# Refused here instead, before a handler runs: a write that says it came from a page
# that is not this UI. Requests with no Origin at all (curl, scripts, the bot's own
# tools) are let through; a browser always sends one on a cross-site write, and
# Sec-Fetch-Site catches the few that do not.
@app.middleware("http")
async def refuse_foreign_writes(request: Request, call_next):
  if request.method not in ("GET", "HEAD", "OPTIONS"):
    origin = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site", "")
    if (origin is not None and not LOCAL_ORIGIN.match(origin)) or fetch_site == "cross-site":
      return JSONResponse(status_code=403,
                          content={"detail": "Refused: request from another site."})
  return await call_next(request)


def inside(base, relative):
  """`relative` resolved under `base`, or a 404 if it would land anywhere else.

  Every route that turns part of a URL into a file path goes through this. The checks
  it replaces were none at all: os.path.join discards its base when handed an absolute
  path ("C:/Windows/win.ini"), ".." walks out of it, and on Windows a backslash inside a
  single URL segment ("..%5cconfig") does too -- which is how a preset or theme name
  reached the live config.json, for reading and, through PUT, for writing.
  """
  root = os.path.realpath(base)
  target = os.path.realpath(os.path.join(root, relative))
  try:
    contained = os.path.commonpath([root, target]) == root and target != root
  except ValueError:                      # a different drive entirely
    contained = False
  if not contained:
    raise HTTPException(status_code=404)
  return target


def named_json(directory, name):
  """`<directory>/<name>.json` for a name that must be a plain file in it, not a path."""
  path = inside(directory, f"{name}.json")
  if os.path.dirname(path) != os.path.realpath(directory):
    raise HTTPException(status_code=404)
  return path

@app.get("/themes")
def list_all_themes():
  themes_dir = "themes"
  custom_themes = []
  default_themes = []
  if not os.path.exists(themes_dir):
    return []
  for filename in os.listdir(themes_dir):
    file_path = os.path.join(themes_dir, filename)
    if not filename.endswith(".json"):
      continue
    try:
      with open(file_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content: continue # Skip empty files
        data = json.loads(content)
        if filename == "umas.json":
          if isinstance(data, list):
            # Filter out any null/empty entries in the list
            default_themes.extend([t for t in data if t and "id" in t])
        else:
          if isinstance(data, dict) and "primary" in data:
            if "id" not in data:
              data["id"] = filename.replace(".json", "")
            custom_themes.append(data)
    except Exception as e:
      print(f"Error loading {filename}: {e}")
  default_themes.sort(key=lambda x: x.get("label", "").lower())
  return custom_themes + default_themes

@app.get("/theme/{name}")
def get_theme(name: str):
  path = named_json(THEMES_DIR, name)
  if not os.path.isfile(path):
    raise HTTPException(status_code=404)
  with open(path, "r", encoding="utf-8") as f:
    return JSONResponse(content=json.load(f))

@app.post("/theme/{name}")
def update_theme(new_theme: dict, name: str):
  # This used to dump `data` -- not the request, but whatever the module-level preset
  # loop below happened to leave in that name, which is a whole config preset. The file
  # is serialised first and only then opened, so nothing is truncated on a failure.
  path = named_json(THEMES_DIR, name)
  text = json.dumps(new_theme, indent=2)
  with open(path, "w", encoding="utf-8") as f:
    f.write(text)
  return {"status": "success", "data": new_theme, "name": name}

@app.get("/config")
def get_config():
  """The config this process actually runs on, not the raw file.

  Layered the way `core.config.load_config` layers it: the template underneath, this
  instance's file over it, machine.json on top. Reading the file alone reported a key it
  does not carry as absent although the process runs it as the template default, and hid
  every machine-wide override -- while the sibling GET /config/setup answered "from the
  file that actually decides it". Two read endpoints, two different answers about the
  same settings.
  """
  return core_config.load_config()

def merge_into_existing(existing: dict, incoming: dict) -> dict:
  """Overlay `incoming` onto `existing`, keeping keys `incoming` says nothing about.

  The web UI only round-trips the settings it knows how to render -- its schema drops
  everything else on parse -- so posting its state verbatim deleted whole config blocks
  from disk. Saving any page of the UI used to wipe the entire independent_training
  section, silently turning the mode off.

  Objects are merged key by key so an untouched sibling survives. Anything else, arrays
  included, replaces wholesale: a list is a single value to the UI, and merging one
  element-wise would make it impossible to remove an entry from it.
  """
  merged = dict(existing)
  for key, value in incoming.items():
    current = merged.get(key)
    if isinstance(current, dict) and isinstance(value, dict):
      merged[key] = merge_into_existing(current, value)
    else:
      merged[key] = value
  return merged

# A cooldown is a cached answer to a question that was asked on screen, and some of those
# answers depend on the config. Turning TP refill back on does not shorten the wait that
# was written while it was off -- the career sits out a wait it no longer needs, still
# explaining itself with a reason that now says the opposite of the config.
#
# Keys are matched to the task whose cooldown they can invalidate. Any change clears it,
# in either direction, rather than only a change that looks more permissive: the bot
# re-reads TP off the home screen and defers again if it was right the first time, which
# is cheap and authoritative in a way that second-guessing the direction here is not.
COOLDOWNS_INVALIDATED_BY = {
    "career": (("independent_training", "tp_refill_enabled"),
               ("independent_training", "tp_refill_max_per_session")),
}


def _at(document, path):
  for key in path:
    if not isinstance(document, dict):
      return None
    document = document.get(key)
  return document


def _drop_cooldowns_the_new_config_invalidates(before, after):
  """Clear cooldowns whose reasons the just-saved config has made untrue."""
  stale = [task for task, paths in COOLDOWNS_INVALIDATED_BY.items()
           if any(_at(before, path) != _at(after, path) for path in paths)]
  if not stale:
    return
  path = schedule_path()
  try:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return  # Nothing scheduled yet, so nothing to invalidate.
  tasks = document.get("tasks", {})
  cleared = [name for name in stale
             if tasks.get(name, {}).get("next_run", 0) > time.time()]
  if not cleared:
    return
  for name in cleared:
    tasks[name]["next_run"] = 0.0
    tasks[name]["reason"] = "the settings this wait was based on changed"
  _write_json_object(path, document)
  print(f"[CONFIG] Cleared the cooldown on {', '.join(cleared)}: "
        f"the settings it was waiting on changed.")


def _apply_saved_config():
  """Re-read the saved file into the config module the bot and this server both use.

  Saving only ever reached disk. reload_config runs at startup and when the bot is
  started, and nowhere else -- so a setting changed while the bot was running did not
  take, and the Overview, which asks build_tasks whether each task is enabled, went on
  reporting whatever was true when the process began. Switching the mission rewards back
  on did nothing twice over: not in the bot, and not in the panel that would have shown
  it had not.

  Failures are reported and swallowed. The file is already written, so the edit is never
  lost -- the worst case is that it applies at the next start, which is exactly what
  happened before this existed. reload_config raises on a missing key, and a save that
  came back 500 having actually saved would be worse than one that quietly waits.

  Returns whether the config was actually re-read. A caller that goes on to say what
  the config "currently" says -- the ADB test's note about the switch -- must ask
  first: a reload that failed leaves the config module holding whatever the process
  last loaded, and quoting that as the config's state is a claim about a file nobody
  just read.
  """
  try:
    core_config.reload_config()
  except Exception as exception:  # noqa: BLE001 - a bad config must not lose the save
    print(f"[CONFIG] Saved, but could not apply it to the running bot: {exception}")
    return False
  # And which emulator this process drives, because that is not read from core.config at
  # use time -- main.py assigns bot.device_id once at startup and again when a run
  # starts. Between those, everything keyed on the device (the schedule file, the
  # Overview's own row, /bot/status) went on naming the old emulator, so Run now after
  # changing the address operated on a schedule belonging to a different device.
  #
  # Not while a run is in progress: the device a career is halfway through is not a
  # setting, and the next run picks the new one up anyway.
  resolve = BOT_CONTROL.get("resolve_device")
  if resolve and not bot.is_bot_running:
    try:
      resolve()
    except Exception as exception:  # noqa: BLE001 - same reasoning as above
      print(f"[CONFIG] Saved, but could not re-read the device: {exception}")
  return True


@app.post("/config")
def update_config(new_config: dict):
  try:
    with open(core_config.config_path(), "r", encoding="utf-8") as f:
      existing = json.load(f)
  except (OSError, ValueError):
    # No readable config to preserve, so the post is all there is to write.
    existing = {}

  merged = merge_into_existing(existing, new_config)
  # use write+ to create file if somehow user deleted it before saving
  # Atomic: config.json is read by this process's own bot thread and, for an instance,
  # by anything the web UI asks next. A torn write here is a config nobody can parse.
  _write_json_object(core_config.config_path(), merged)
  # Outside the `with`, so the file is closed and flushed before anything reads it back.
  # Keys machine.json already sets are written there as well. It is layered on top at
  # load time, so a change saved only into this instance's file is a change the bot never
  # sees: the page showed the new webhook URL, said "Saved", and every notification kept
  # going to the old one. The same rule POST /config/setup uses for its own keys, and as
  # there, only keys machine.json already carries -- adding one would quietly widen what
  # a single instance decides for all the others.
  machine = _read_json_object(core_config.MACHINE_PATH)
  shared_now = [key for key in core_config.MACHINE_OWNED
                if key in machine and key in new_config]
  if shared_now:
    for key in shared_now:
      machine[key] = new_config[key]
    _write_json_object(core_config.MACHINE_PATH, machine)
    print(f"[CONFIG] Also written to {core_config.MACHINE_PATH}, which decides these "
          f"for every instance: {', '.join(shared_now)}")

  _drop_cooldowns_the_new_config_invalidates(existing, merged)
  _apply_saved_config()
  return {"status": "success", "data": merged}

# The Setup page's keys that belong to one emulator rather than to the PC. setup.json is
# shared by every instance, and it used to hold these too -- so each instance's UI showed
# and saved the same device, and every auto-save wrote it into that instance's own file.
PER_INSTANCE_SETUP_KEYS = ("use_adb", "window_name", "device_id", "preset_id")


def _read_json_object(path):
  """The file as a dict, or an empty one.

  Several callers merge this with an edit and write the result back, so "could not read
  it" and "it is empty" have the same consequence here: the file is replaced by the edit
  alone. On Windows a file being swapped into place denies readers for the length of the
  swap, which made that a real way to lose a config -- hence the retry underneath.
  """
  return read_json_retrying(path) or {}


def _unparseable_or_object(path):
  """The file as a dict to merge an edit into, or None when it must not be rewritten.

  None only for a file that exists and cannot be read even after the retry: merging an
  edit into that would replace the whole file with the edit. A file that does not exist
  yet is an empty dict, since there is nothing in it to lose.
  """
  loaded = read_json_retrying(path)
  if loaded is None and os.path.isfile(str(path)):
    return None
  return loaded or {}


def _write_json_object(path, data):
  """Written whole or not at all, through a temporary name nobody else holds.

  The uniqueness matters as much as the swap here: this runs on a uvicorn request thread
  and writes schedule.json, which the bot thread writes too. See core/atomic_write.py.
  """
  write_json_atomic(path, data)


def _machine_setup_keys():
  return [key for key in SETUP_KEYS if key in core_config.MACHINE_OWNED]


@app.get("/config/setup")
def get_setup_config():
  """The Setup page's values, each from the file that actually decides it.

  A named instance's device settings come from its own config file. Machine-wide ones
  come from machine.json where it sets them, because it overrides every instance at load
  time -- showing setup.json's value there would show a setting that is not in force.
  With neither an instance nor a machine.json, this is setup.json exactly, as before.
  """
  # Less anything retired. setup.json is written by merging the page into what was there,
  # so a setting removed from the build stays in the file -- and served from here, the
  # page carried it as an unknown key and could never agree with itself that there was
  # nothing to save.
  setup = {key: value for key, value in _read_json_object(GLOBAL_SETUP_PATH).items()
           if key not in RETIRED_KEYS}
  if bot.instance_name:
    # Every setup key, not just the device ones. An instance file is a whole config and
    # is the only thing `load_config` reads, so a key that lived in setup.json alone
    # never reached the running bot: the page showed the saved value, the bot kept using
    # the old one, and there was nothing to say the two disagreed. Eight of the twelve
    # were in that state -- OCR device, notification sounds and volume, sleep multiplier,
    # the update check.
    own = _read_json_object(core_config.config_path())
    for key in SETUP_KEYS:
      if key in own:
        setup[key] = own[key]
  machine = _read_json_object(core_config.MACHINE_PATH)
  for key in _machine_setup_keys():
    if key in machine:
      setup[key] = machine[key]
  return setup


@app.post("/config/setup")
def update_setup_config(new_setup_config: dict):
  """Save the Setup page, each value to the file it belongs to."""
  if bot.instance_name:
    own_path = core_config.config_path()
    own = _unparseable_or_object(own_path)
    if own is None:
      # Guarded exactly as the default branch below is: merged into the empty result, the
      # page's setup keys became the whole file, and the next start healed everything
      # else back to the template.
      print(f"[CONFIG] {own_path} could not be read, so the settings above were not "
            "written to it. Fix the file, then save again.")
    else:
      # Every setup key goes into the instance's own file, because that file is what the
      # process actually runs on. Writing only the device keys here left the other eight
      # saved somewhere nothing reads.
      for key in SETUP_KEYS:
        if key in new_setup_config:
          own[key] = new_setup_config[key]
      _write_json_object(own_path, own)
    # The shared file keeps the default instance's device: only the keys that are not
    # per-instance are taken from this instance's page.
    shared = _read_json_object(GLOBAL_SETUP_PATH)
    shared.update({key: value for key, value in new_setup_config.items()
                   if key not in PER_INSTANCE_SETUP_KEYS})
  else:
    # Merged, not replaced. Replacing deleted any key the file carried that the page did
    # not send -- a key added by hand, or one from a newer build -- while the named
    # branch above has always merged. Two behaviours for one file is one too many.
    shared = _read_json_object(GLOBAL_SETUP_PATH)
    shared.update(new_setup_config)
    # And into the file this process runs on. For the default instance that is
    # config.json, which load_config reads and setup.json is not in -- so a save that
    # reached setup.json alone was a setting the bot never saw: the page said ADB, the
    # ADB test (which asks the page) connected, and Start still went hunting for a game
    # window. The named branch writes its own file for the same reason.
    own_path = core_config.config_path()
    own = read_json_retrying(own_path)
    if own is None and os.path.isfile(own_path):
      # A config the page cannot parse is not one an innocent save gets to rewrite:
      # merging the page's keys with the empty result would reset the whole file to
      # those keys, and the next start would self-heal it back to the template's
      # values with the user's edits gone. The file is left as it is, and the next
      # start's "Could not read the config" is the report of it.
      print(f"[CONFIG] {own_path} could not be read, so the settings above were not "
            "written to it. Fix the file, then save again.")
    else:
      own = own or {}
      # preset_id excepted: Apply is the only thing that may write that pointer
      # (set_applied_preset_id), and the default page's copy of it is a mirror Apply
      # never updates -- carrying it back would point the instance elsewhere and undo
      # the Apply. The shared file keeps the mirror for the page.
      for key in SETUP_KEYS:
        if key in new_setup_config and key != "preset_id":
          own[key] = new_setup_config[key]
      _write_json_object(own_path, own)
  # Retired settings go on the way out, or the merge above keeps them forever: the startup
  # heal cleans config.json and the presets, but this file is only ever written here.
  for key in RETIRED_KEYS:
    shared.pop(key, None)
  _write_json_object(GLOBAL_SETUP_PATH, shared)

  # Only the keys machine.json already sets. It overrides every instance at load time,
  # so an edit to one of those that reached setup.json alone would look saved and change
  # nothing. Keys it does not set are left out of it: adding them would quietly widen
  # what one instance's page decides for all the others.
  machine = _read_json_object(core_config.MACHINE_PATH)
  owned = [key for key in _machine_setup_keys() if key in machine and key in new_setup_config]
  if owned:
    for key in owned:
      machine[key] = new_setup_config[key]
    _write_json_object(core_config.MACHINE_PATH, machine)

  # Both branches, not just named instances: a save that applies only to the page is a
  # setting that looks saved and is not in force -- which is what the default
  # instance's ADB switch did.
  _apply_saved_config()
  return {"status": "success", "data": get_setup_config()}


def _save_tested_device(device_addr):
  """Persist the address a green ADB test verified, on the path a Setup save uses.

  The test proved the emulator at device_addr answers and hands back frames, and a
  green test is the moment the user means it to become the bot's device -- so the
  address goes exactly where a save of the page's value would have written it: this
  process's own config file, plus the shared setup.json for the default instance. A
  named instance writes its own file alone: the shared file is the default instance's
  device, and a test run from one instance's page proved nothing about the others.

  One key only: device_id is merged in, and every other key the files carry is left
  as it is. A file the reader cannot parse is guarded, not written -- the same guard
  a Setup save carries -- and the guard is part of the answer, not a side effect on
  the console: a save that wrote nothing must not be answered with "Saved". Returns
  what happened to each file, for the response to claim only that:

  {"own": "saved" | "already" | "blocked",    -- the file this process runs on, the
    only one the bot reads. "blocked" = it exists but cannot be parsed, so nothing
    was written to it.
   "shared": "saved" | "already" | "blocked" | None}  -- the default instance's
    mirror; None for a named instance, which writes no shared file.
  """
  own_path = core_config.config_path()
  own = read_json_retrying(own_path)
  report = {"own": "blocked", "shared": None}
  if own is None and os.path.isfile(own_path):
    # The same guard the Setup save carries: a config the reader cannot parse is not
    # one a test gets to reset to a single key, with the user's edits gone and the
    # next start self-healing the file back to the template's values. It is left as
    # it is, and the route reports the guard in the response -- "Saved" here would
    # name an address nothing on disk carries.
    print(f"[CONFIG] {own_path} could not be read, so the tested device was not "
          "written to it. Fix the file, then test again.")
  else:
    already = own is not None and own.get("device_id") == device_addr
    if not already:
      own = own or {}
      own["device_id"] = device_addr
      _write_json_object(own_path, own)
    report["own"] = "already" if already else "saved"
  if not bot.instance_name:
    # The default instance's shared file keeps the page's copy of the device, exactly
    # as a Setup save does. Written only when it disagrees, so a repeat test of the
    # saved address leaves it byte and mtime untouched.
    shared = read_json_retrying(GLOBAL_SETUP_PATH)
    if shared is None and os.path.isfile(GLOBAL_SETUP_PATH):
      # The same guard the own file's write just above carries: a setup.json the
      # reader cannot parse is not one a test gets to reset to a single key, with
      # every other key the file carries gone. It is left as it is, and the [CONFIG]
      # line is the report of it. Unlike the own config, nothing self-heals this
      # file at start -- which is exactly why a silent reset here would be final.
      print(f"[CONFIG] {GLOBAL_SETUP_PATH} could not be read, so the tested device was "
            "not written to it. Fix the file, then test again.")
    else:
      already = shared is not None and shared.get("device_id") == device_addr
      if not already:
        shared = shared or {}
        shared["device_id"] = device_addr
        _write_json_object(GLOBAL_SETUP_PATH, shared)
      report["shared"] = "already" if already else "saved"
  return report


@app.get("/instance")
def get_instance():
  """Which instance this page is talking to, for the banner and the hotkey hint."""
  from utils.log import args
  return {
    "declared": bool(bot.instance_name),
    "name": bot.instance_label(),
    "port": getattr(bot, "port", None),
    "hotkey": getattr(bot, "hotkey", "f1"),
    "use_adb": bool(getattr(bot, "use_adb", False)),
    "device_id": getattr(bot, "device_id", None) if getattr(bot, "use_adb", False) else None,
    # --use-adb wins over the config at runtime, so the Setup page's device is not the
    # one in use while it is given. Worth saying where the device is shown.
    "device_from_command_line": bool(getattr(args, "use_adb", None)),
    # Whether this instance's own bot is live. A sibling probing this port reads it to
    # know whether updating the shared checkout would move code out from under a career.
    "bot_running": bool(getattr(bot, "is_bot_running", False)),
    # The version this process started on (the boot stamp). Only the current tab shows
    # it: each sibling is served by its own process, which alone knows what it stamped.
    "version": BOOT_VERSION,
    "config_file": core_config.config_path(),
    **_applied_preset(),
  }


def _applied_preset():
  """This instance's applied preset: its id, and its name as the preset list shows it."""
  preset_id = _read_json_object(core_config.config_path()).get("preset_id") or ""
  name = next((cfg["name"] for cfg in CURRENT_CONFIGS if cfg["id"] == preset_id), None)
  return {"preset_id": preset_id, "preset_name": name}


@app.post("/instances")
def create_instance(request: dict):
  """A new instance's config, copied from this one's settings with its own device."""
  from core import instances
  try:
    path = instances.create(request.get("name"), request.get("device_id"),
                            core_config.config_path(), core_config.INSTANCE_DIR)
  except instances.InstanceError as error:
    raise HTTPException(status_code=400, detail=str(error))
  return {"status": "success", "name": request.get("name", "").strip(), "config_file": path}


@app.post("/instances/{name}/launch")
def launch_instance(name: str):
  """Start an instance that is not running, on a free port, detached from this one."""
  from core import instances
  try:
    name = instances.validate_name(name)
  except instances.InstanceError as error:
    raise HTTPException(status_code=400, detail=str(error))
  live = {row["name"]: row for row in live_instances()["instances"] if row.get("running")}
  if name in live:
    raise HTTPException(status_code=409,
                        detail=f"'{name}' is already running on :{live[name]['port']}.")
  taken = {row["port"] for row in live.values() if row.get("port")}
  try:
    port = instances.free_port(
        [p for p in INSTANCE_PORTS if p not in taken])
  except instances.InstanceError as error:
    raise HTTPException(status_code=400, detail=str(error))
  try:
    process = instances.launch(name, port, instance_dir=core_config.INSTANCE_DIR)
  except instances.InstanceError as error:
    instances.release_port(port)
    raise HTTPException(status_code=400, detail=str(error))

  # Confirmed rather than assumed. This used to answer "success" as soon as a process
  # existed, so a worker that died on start surfaced only as a generic timeout a minute
  # later, with the reason sitting unread in its console log.
  outcome = instances.await_start(name, port, process, _probe_instance)
  if outcome != "starting":
    instances.release_port(port)
  if outcome == "failed":
    tail = instances.tail(instances.console_log(name), 15) or "(it printed nothing)"
    raise HTTPException(status_code=500,
                        detail=f"'{name}' exited while starting. The end of its output:\n{tail}")
  return {"status": outcome, "name": name, "port": port, "pid": process.pid,
          "log": instances.console_log(name)}


def _device_users(exclude_self=False):
  """Which instances are set to each device, from their config files. {device: [names]}.

  exclude_self leaves out the config this process runs on. The Setup page asks for
  that: it is this instance's own page, and a device it is itself using is not
  "already set" by itself. The add-instance dialog asks without it, because a second
  bot on the emulator this one drives is a conflict the dialog exists to catch.
  """
  own_name = bot.instance_name or ""
  users = {}
  for instance, path in _instance_config_files():
    if exclude_self and instance == own_name:
      continue
    own = _read_json_object(path)
    if own.get("use_adb") and own.get("device_id"):
      users.setdefault(own["device_id"], []).append(instance or "default")
  return users


@app.get("/adb/devices")
def adb_devices(scan: bool = False, exclude_self: bool = False):
  """Emulators adb can see, each with the instances already set to it.

  `scan` first tries the ports the common emulators use, which finds one adb has not been
  told about yet. Only on request -- it is a burst of connection attempts.

  `exclude_self` drops this process's own instance from `used_by`, for the Setup page:
  its own device arriving as "used by itself" read as a conflict with nobody. The
  add-instance dialog leaves it in (the default), where this instance's device is a real
  conflict for the instance being added.
  """
  from core import instances
  try:
    devices = instances.list_devices(scan=scan)
  except Exception as error:                                       # noqa: BLE001
    raise HTTPException(status_code=503, detail=f"adb is not answering: {error}")
  users = _device_users(exclude_self=exclude_self)
  # Users are pooled across every address of one emulator: an instance set to :5555 is
  # using the emulator that also answers on :16384, and the dialog must say so on both.
  by_identity = {}
  for device in devices:
    if device.get("identity"):
      by_identity.setdefault(device["identity"], []).extend(users.get(device["serial"], []))
  for device in devices:
    pooled = by_identity.get(device.get("identity")) if device.get("identity") else None
    device["used_by"] = sorted(set(pooled if pooled is not None else users.get(device["serial"], [])))
  return {"devices": devices}


@app.post("/adb/probe")
def adb_probe(request: dict):
  """Check an emulator address without re-pointing this instance's own device."""
  from core import instances
  return instances.probe_device(request.get("device_id"))


@app.get("/instances/{name}/log")
def instance_log(name: str, kind: str = "log", lines: int = 200):
  """The end of an instance's log, or of the console output it was launched with."""
  from core import instances
  try:
    path = instances.log_path(name, kind)
  except instances.InstanceError as error:
    raise HTTPException(status_code=400, detail=str(error))
  text = instances.tail(path, lines)
  if text is None:
    return {"name": name, "kind": kind, "exists": False, "text": ""}
  return {"name": name, "kind": kind, "exists": True, "text": text}


@app.post("/instance/shutdown")
def shutdown_instance():
  """Stop this instance's bot and end its process. Named instances only.

  The unnamed instance is the one start.bat opened in a console, and usually the page the
  user is looking at; closing it from its own page would leave that page talking to
  nothing. It is closed the way it was opened.
  """
  if not bot.instance_name:
    raise HTTPException(status_code=400,
                        detail="The default instance is closed from its console window.")
  import threading
  if getattr(bot, "is_bot_running", False):
    try:
      _control("stop")()
    except Exception:                                              # noqa: BLE001
      pass

  def leave():
    time.sleep(1.0)             # let this response reach the page first
    os._exit(0)

  threading.Thread(target=leave, daemon=True).start()
  return {"status": "stopping", "name": bot.instance_name}


# The ports main.py scans for a free one. An instance pinned with --port outside them is
# not found here; the hub that launches instances from the UI assigns ports inside them.
from core.instances import INSTANCE_PORTS  # noqa: E402 -- one list, shared with the launcher


def _probe_instance(port, timeout=0.5):
  """What the instance on `port` says about itself, or None if nothing answers there."""
  import urllib.request
  try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/instance", timeout=timeout) as reply:
      data = json.loads(reply.read().decode("utf-8"))
  except Exception:                                                # noqa: BLE001
    return None
  if not isinstance(data, dict) or "name" not in data:
    return None
  # The port that answered, not the one it reports, which is None until start_server
  # has chosen one.
  data["port"] = port
  return data


def _other_instances():
  """Which sibling instances have a live bot, as {name: True}.

  Asked of every other web port this instance could share a checkout with -- INSTANCE_PORTS
  minus our own. A port that does not answer is simply absent: an instance that is not
  running cannot be running a career. This is the only place in the update path that talks
  to another process; core/updater.py takes the mapping and never reaches out itself.
  """
  from concurrent.futures import ThreadPoolExecutor
  own_port = getattr(bot, "port", None)
  others = [port for port in INSTANCE_PORTS if port != own_port]
  with ThreadPoolExecutor(max_workers=len(others) or 1) as pool:
    answers = list(pool.map(_probe_instance, others))
  return {answer["name"]: True for answer in answers
          if answer and answer.get("bot_running")}


@app.get("/instances/live")
def live_instances():
  """Every instance the UI can switch to: those answering on a port, then the rest.

  Asked of the ports rather than read off files, because what the tabs need is what is
  running *now* -- a claim or state file outlives the process that wrote it. Instances
  with a config file but no process are listed as not running, so a tab does not simply
  vanish when its bot is closed.
  """
  from concurrent.futures import ThreadPoolExecutor
  own = dict(get_instance(), current=True, running=True)
  # Version: only the current row carries a stamp (from get_instance(), the boot
  # stamp). Each sibling is served by its own process, which alone knows what it
  # stamped at startup -- so this process does not guess on their behalf.
  others = [port for port in INSTANCE_PORTS if port != own["port"]]
  with ThreadPoolExecutor(max_workers=len(others) or 1) as pool:
    answers = list(pool.map(_probe_instance, others))
  rows = [own] + [dict(answer, current=False, running=True) for answer in answers if answer]

  running = {row["name"] for row in rows if row.get("declared")}
  try:
    configured = sorted(Path(core_config.INSTANCE_DIR).glob("*.json"))
  except OSError:
    configured = []
  for path in configured:
    if path.stem not in running:
      rows.append({"name": path.stem, "declared": True, "current": False, "running": False,
                   "port": None, "hotkey": None, "device_id": None, "use_adb": None,
                   "device_from_command_line": False, "config_file": str(path),
                   "preset_id": _read_json_object(str(path)).get("preset_id") or ""})

  # The default instance -- config.json, no name -- first, then the rest by name, so the
  # tabs keep their order whichever one the page happens to be served from.
  rows.sort(key=lambda row: (row.get("declared", False), row["name"].lower()))
  return {"instances": rows}

CURRENT_CONFIGS=[]
GLOBAL_NUMBER = 100_000
CONFIG_PATTERN = re.compile(r'^config_(\d+)$')
def add_config_to_global_list(config_dict):
  global GLOBAL_NUMBER, CURRENT_CONFIGS, CONFIG_PATTERN
  CURRENT_CONFIGS.append(config_dict)
  GLOBAL_NUMBER = 100_000

  def sort_key(item):
    global GLOBAL_NUMBER
    match = CONFIG_PATTERN.match(item["id"])
    if match:
      return int(match.group(1))
    GLOBAL_NUMBER += 1
    return GLOBAL_NUMBER

  CURRENT_CONFIGS.sort(key=sort_key)

# find the next gap in the configs and return that
def get_next_config_id(taken=()):
  """The lowest free preset number.

  Read off the disk rather than off CURRENT_CONFIGS, which is built once at import and
  never rescanned: several instance processes share one config/ directory, so a second
  process holding a stale list handed out a number the first had already used, and the
  create then overwrote that file. `taken` lets a caller exclude numbers it has just
  tried and found occupied.
  """
  used = {int(match.group(1))
          for path in Path(CONFIG_DIR).glob("config_*.json")
          for match in [CONFIG_PATTERN.match(path.stem)] if match}
  used |= {int(match.group(1))
           for cfg in CURRENT_CONFIGS
           for match in [CONFIG_PATTERN.match(cfg["id"])] if match}
  used |= set(taken)
  expected = 1
  while expected in used:
    expected += 1
  return expected


def create_preset_file(build):
  """Write a new preset at the lowest free number, and return (id, name).

  Created with O_EXCL and retried, so two processes racing for the same number end with
  two presets rather than one overwriting the other. `build` is handed the number and
  returns the document to write.
  """
  tried = []
  for _ in range(20):
    number = get_next_config_id(tried)
    path = f"{CONFIG_DIR}/config_{number}.json"
    document = build(number)
    try:
      handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
      tried.append(number)
      continue
    # The exclusive create claims the number; it does not make the write whole. Filling
    # the claimed file directly left a truncated preset behind if the process died
    # mid-write, and a preset that will not parse is skipped by the startup scan -- so it
    # vanished from the dropdown with nothing said. Claim, then swap the content in.
    os.close(handle)
    try:
      write_json_atomic(path, document)
    except OSError:
      try:
        os.remove(path)
      except OSError:
        pass
      raise
    return f"config_{number}", document["config_name"]
  raise HTTPException(status_code=500,
                      detail="Could not find a free preset number; 20 were taken while "
                             "trying. Delete some presets and try again.")

# What counts as a preset, decided by name rather than by elimination. This used to be
# "every .json under config/ except presets.json and setup.json", which meant anything
# else dropped in there was taken for a preset and "repaired" into one -- given the whole
# template, stripped of its setup keys, and listed in the UI dropdown. Two files landed
# in that trap:
#
#   machine.json      the machine-wide overrides. load_config layers it on top of
#                     everything, so injecting ~41 template keys into it silently
#                     overrode every instance's own settings with template defaults,
#                     and the damage was self-sustaining.
#   update_check.json the shared update-check cache, added 2026-09-20. Grew from 208
#                     bytes to 19kB and appeared in the dropdown as a preset called
#                     "Preset", which a user could apply.
#
# Presets are written by this file and only ever named `default.json` or `config_<n>.json`
# -- see add_config() and duplicate_config() below -- so that is the rule.
PRESET_STEM = re.compile(r"^(default|config_\d+)$")


def preset_files():
  """Every preset file under config/, oldest naming scheme included, sorted by name."""
  return sorted((p for p in Path(CONFIG_DIR).glob("*.json")
                 if p.is_file() and PRESET_STEM.match(p.stem)),
                key=lambda p: p.stem.lower())


def _warn_about_machine_config():
  """Say so if machine.json carries keys that are not machine-wide.

  Reported rather than repaired: some of those keys may be a deliberate override, and
  some are this bug's leavings, and nothing here can tell which. `core/config.py` names
  what machine.json is allowed to own.
  """
  machine = _read_json_object(core_config.MACHINE_PATH)
  strays = sorted(k for k in machine if k not in core_config.MACHINE_OWNED)
  if not strays:
    return
  print(f"[WARN] {core_config.MACHINE_PATH} sets {len(strays)} key(s) that are not "
        f"machine-wide: {', '.join(strays[:6])}{' ...' if len(strays) > 6 else ''}")
  print("[WARN] Every instance on this box takes those values instead of its own. "
        "If you did not put them there, a bug before 2026-09-20 did; remove them.")


#populate global config list once
for file_path in preset_files():
  try:
    data = _update_config(str(file_path))
  except (OSError, ValueError) as problem:
    # One unreadable preset must not stop every instance on the box from starting. The
    # scan runs at import, before main.py has done anything, so an uncaught error here
    # took down the process with a traceback that did not even name the file.
    print(f"[WARN] Skipping {file_path}: {problem}")
    continue
  config_dict = {"id": Path(file_path).stem, "name": data.get("config_name", file_path.stem)}
  add_config_to_global_list(config_dict)

_warn_about_machine_config()

"""
# example for a middleware that prints something evertime a request comes in
@app.middleware("http")
async def print_current_configs(request: Request, call_next):
    global CURRENT_CONFIGS
    print(CURRENT_CONFIGS)
    return await call_next(request)
"""

def _adb_test_note(device_addr):
  """A note for when the tested address is not the one in this process's saved config.

  A test that connected but could not read the screen saves nothing, so the bot's next
  run still reads its device from the process's own config file. The note names the
  address the bot actually runs on -- the saved one, or the default when the saved
  value is empty -- so a test on another address is not read as "the bot is on
  <tested>" while it is still on the saved one. It advises no save: a Use-triggered
  test runs while the page holds the new address unsaved and the page's own auto-save
  (~1s later) writes it, so a "save the page" instruction is stale or already carried
  out by the time it is read -- and inviting a save of an address no frame verified is
  what a red test must not do. The note states only what the test itself did -- saved
  nothing -- and which address the bot reads. A process pinned by --use-adb drives the
  flag's address instead, and no save can re-point it, so the note names that.
  """
  from utils.log import args
  cli = getattr(args, "use_adb", None)
  if cli:
    if device_addr == cli:
      # Testing the flag's own address is no divergence: the process runs on exactly
      # what was tested.
      return ""
    return (f" Note: this process drives '{cli}' (--use-adb), not '{device_addr}'. "
            "The saved config governs launches without --use-adb.")
  try:
    with open(core_config.config_path(), encoding="utf-8") as fh:
      saved = json.load(fh).get("device_id", "")
  except (OSError, ValueError):
    saved = ""
  if saved == device_addr:
    return ""
  if not saved:
    # The saved value is empty, so the bot does not run on "" -- it drives the default
    # address. Name that, or the note tells the user the bot is on nothing. (Testing the
    # default address itself is not a divergence: the bot drives exactly what was tested.)
    if device_addr == "127.0.0.1:5555":
      return ""
    return (f" Note: the saved Device ID is empty, so the bot runs on the default "
            f"'127.0.0.1:5555', not '{device_addr}'. This test saved nothing.")
  return (f" Note: the bot runs on '{saved}' (the saved config), not '{device_addr}'. "
          f"This test saved nothing.")


@app.post("/adb/test")
async def test_adb(request: Request):
  """Attempt to connect to the ADB device, read the screen, and report.

  Takes the device_id from the request body so the button works on an address just
  typed in and not yet saved. If the request body is empty, it falls back to the
  device_id in the current config.json.

  A fully green test (connected and read a frame) is the moment the tested address
  becomes the bot's device: it is persisted on the same path a Setup save uses -- the
  process's own config file, plus the shared setup.json for the default instance --
  and the process keeps it instead of restoring the pre-test value. A test that
  connected but could not read a screen, or could not connect at all, saves nothing
  and says which device the bot still runs on. Those two are not the same failure and
  the report does not treat them as one: a device that connected and could not be read
  is refused with the reason the device layer reported -- wrong size, no readable frame,
  or a right-sized frame with nothing drawn on it -- and only a connection that never
  opened gets the sentence about the emulator not running, which is the case that
  sentence describes. A config the save cannot parse is guarded, not written, and the
  response says exactly what was and was not written: a green frame alone does not make
  an address the bot's device, so "Saved" appears only when the config the bot reads
  actually took it.
  """
  import utils.adb_actions as adb_actions
  from utils.adb_actions import init_adb, screenshot
  body = {}
  try:
    body = await request.json()
  except Exception:
    pass

  device_addr = str(body.get("device_id", "")).strip()
  if not device_addr:
    try:
      with open(core_config.config_path(), encoding="utf-8") as fh:
        device_addr = json.load(fh).get("device_id", "")
    except (OSError, ValueError):
      device_addr = ""
  if not device_addr or device_addr == "":
    return {"status": "fail", "detail": "No Device ID provided."}

  import core.bot as bot
  # The bot-state lock is held across the whole sequence below -- the flag check, the
  # device re-point, the connect and read, the green test's save, and the finally's
  # restore-or-keep decision. start_bot sets is_bot_running under the same lock, so
  # the two paths order completely instead of both trusting one read of the flag: a
  # start slipping in between the check and the re-point began a run on device fields
  # the test was about to overwrite, and a green one then persisted the tested address
  # to the config and the shared file with the restore skipped -- a live session
  # re-pointed, and on disk. Held for the whole test rather than just the check,
  # because the window it closes is the device fields, not the flag alone: a start
  # parked on this lock resumes onto the state the test finished with.
  with bot.bot_state_lock:
    if bot.is_bot_running:
      return {"status": "fail", "detail": "The bot is running. Stop it (F1) before "
              "testing a connection -- the test would otherwise re-point a live "
              "session's device mid-run."}
    prev_use_adb, prev_device_id = bot.use_adb, bot.device_id
    prev_device_is_default = bot.device_id_is_default
    save_state = ""     # "saved"/"already" once the config took the address, "blocked"
                        # when its write was guarded off, "" before any save was tried
    try:
      bot.use_adb = True
      bot.device_id = device_addr
      bot.device_id_is_default = False     # the test drives the tested address on purpose
      if not init_adb():
        # init_adb() answers one bool for failures that are not the same failure: the
        # connection itself never opened, or the device answered and handed back a frame
        # nothing could be read from -- wrong size, unreadable, or right-sized with no
        # picture on it. This branch explained all of them the first way. The device that
        # it went wrong for was connected: it had answered ADB and returned a correctly
        # sized frame with an empty screen, and the page told its owner to ensure the
        # emulator was running and ADB was enabled -- the two things that were already
        # true, and the opposite of what to go and look at. The reason is carried out of
        # the module verbatim, which is the same message the log carries, so the page and
        # the log cannot end up with two accounts of one refusal. The fixed sentence stays
        # for the case it was written for: a connection that did not open.
        problem = adb_actions.connect_problem()
        if problem:
          # Named the way the branch further down names a device that connected and could
          # not be captured: the test saved nothing, so the bot still runs on the address
          # in its config, and a red test that leaves the device where it was must say so
          # rather than let the page's unsaved value be read as the bot's device.
          return {"status": "fail", "detail": problem + _adb_test_note(device_addr)}
        return {"status": "fail", "detail": f"Could not connect to '{device_addr}'. "
                "Ensure the emulator is running and ADB is enabled."}

      try:
        frame = screenshot()
        h, w = frame.shape[:2]
      except Exception as exc:
        # Red, not green. This answered "success" with the failure in the small print, so
        # the page showed a device it could not read in the colour that means "use this".
        # Only the capture is inside this try: anything after it failing is not a capture
        # failure, and was reported as one.
        detail = (f"Connected to '{device_addr}', "
                  f"but could not capture a screenshot: {exc}")
        detail += _adb_test_note(device_addr)
        return {"status": "fail", "detail": detail}

      try:
        # Green means the emulator at this address answers and reads frames: it is the
        # address the bot should run on from now. Persist it where a save of the page
        # would have written it, instead of leaving it in a response that is gone when
        # the page is and that the next start never reads.
        save = _save_tested_device(device_addr)
        save_state = save["own"]     # "saved", "already", or "blocked": the config's write
        applied = _apply_saved_config()
        detail = f"Connected to '{device_addr}'. Screen = {w}x{h}"
        if (w, h) != (800, 1080):
          detail += (f" (expected 800x1080. Please adjust your emulator resolution to "
                     f"800x1080 to avoid coordinate/OCR misalignment.)")
        from utils.log import args
        cli = getattr(args, "use_adb", None)
        if cli and cli != device_addr:
          # A --use-adb process cannot be re-pointed by a save: resolve_device() pins it
          # back to the command-line address -- _apply_saved_config just called it, above.
          # The save governs launches without the flag, and the report says that instead
          # of claiming the bot will now run on the tested address.
          if save_state == "already":
            detail += (f" The config already names '{device_addr}' for launches without "
                       f"--use-adb. This process keeps driving '{cli}' (--use-adb).")
          elif save_state == "saved":
            detail += (f" Saved to the config, for launches without --use-adb. This "
                       f"process keeps driving '{cli}' (--use-adb).")
          else:
            detail += (f" The config could not be read, so nothing was saved to it. This "
                       f"process keeps driving '{cli}' (--use-adb).")
        elif save_state == "already":
          detail += f" The bot already runs on '{device_addr}'."
        elif save_state == "saved":
          detail += f" Saved: the bot will now run on '{device_addr}'."
        elif save["shared"] == "saved":
          # The guard in _save_tested_device left the config the bot runs on untouched,
          # so the green frame is all this test achieved, and only setup.json -- which
          # the page mirrors and the bot never reads -- took the address. The response
          # says so where it would have said "Saved": an address nothing the bot reads
          # carries is not one the bot will run on.
          detail += (f" Only setup.json was written: {core_config.config_path()} could "
                     "not be read, so the tested address did not reach the bot. Fix that "
                     "file, then test again.")
        else:
          detail += (f" Nothing was saved: {core_config.config_path()} could not be "
                     "read. Fix that file, then test again.")
        if applied and not getattr(core_config, "USE_ADB", False):
          # Persisting the address cannot switch the bot onto ADB: use_adb is the
          # page's own switch, and it stays where the user left it. A --use-adb process
          # keeps driving ADB whatever the switch says, so the window claim is scoped to
          # the launches the switch actually governs. And the claim quotes the config's
          # current switch, which only a reload that applied can vouch for -- after a
          # failed one the module still holds whatever the process last loaded.
          if cli and cli != device_addr:
            detail += (" ADB is off in the config — launches without --use-adb keep "
                       "using the window until it is switched on.")
          else:
            detail += (" ADB is currently off in the config — the bot will keep using "
                       "the window until you switch it on.")
        return {"status": "success", "detail": detail}
      except Exception as exc:
        # The frame was read, so this is the save or the reload failing, and it says so.
        # Whether anything reached the config is exactly what save_state records, and the
        # finally below restores the process unless it did.
        return {"status": "fail",
                "detail": f"Connected to '{device_addr}' and read the screen, but saving "
                          f"it as the bot's device failed: {exc}" + _adb_test_note(device_addr)}
    finally:
      if save_state not in ("saved", "already"):
        # A test that persisted nothing leaves the process where it found it: refused,
        # failed to connect, or blocked by a config it could not read -- in every one of
        # those, nothing on disk names the tested address, so the process must not go on
        # driving it. One that saved must not restore: the tested address is now the
        # saved device, and pointing the process back would aim it -- and everything
        # keyed on the device, the schedule file included -- at one the config no
        # longer names.
        bot.use_adb, bot.device_id = prev_use_adb, prev_device_id
        bot.device_id_is_default = prev_device_is_default
      # The test cached a frame that no longer reflects the game by the time the bot
      # starts. Every consumer flushes before its own captures today, but the cache is
      # this module's to clean up after itself.
      adb_actions.cached_screenshot = []


@app.post("/webhook/test")
async def test_webhook(request: Request):
  """Send one test message and report what Discord said.

  Takes the URL from the request rather than from the saved config, so the button works
  on a URL just typed in and not yet saved -- which is the moment it is most useful.
  """
  from utils.webhook import send_test
  try:
    body = await request.json()
  except Exception:  # noqa: BLE001 - an empty body means "use the saved URL"
    body = {}
  ok, detail = send_test(str(body.get("url", "")))
  return {"status": "success" if ok else "fail", "detail": detail}


@app.get("/stats/runs")
def get_stat_runs():
  """Every recorded career, oldest first.

  Returned whole rather than filtered by range: a year of heavy use is a few hundred
  kilobytes, and aggregating in the browser means the range tabs switch without a round
  trip.
  """
  # Every device's history, not just this instance's: the page belongs to the person,
  # who has one set of careers however many emulators ran them.
  from core.independent_stats import read_all_runs
  return {"runs": read_all_runs()}


@app.post("/stats/reset")
def reset_stat_runs():
  """Clear the run history. The file is moved aside rather than deleted."""
  from core.independent_stats import clear_all_runs
  return {"status": "success" if clear_all_runs() else "fail"}


@app.get("/configs")
@app.get("/configs/")
def get_configs():
  global CURRENT_CONFIGS
  return {"configs": CURRENT_CONFIGS}

@app.get("/config/applied-preset")
def get_applied_preset_id():
  preset_id = _read_json_object(core_config.config_path()).get("preset_id") or ""
  return {"preset_id": preset_id}


@app.post("/config/applied-preset")
def set_applied_preset_id(body: dict):
  """Point this instance at a preset. The one write that makes an Apply stick.

  Applying is the only moment the pointer may change, so it is the only thing allowed to
  write it. Auto-save deliberately does not: editing a preset that is not applied used to
  repoint the instance at it, and the guard that stopped that also stopped Apply itself
  from ever writing the pointer -- the UI marked the new preset active, the disk kept the
  old one, and a refresh put it back. That is what this route is for.
  """
  preset_id = str(body.get("preset_id") or "")
  if not preset_id:
    raise HTTPException(status_code=400, detail="No preset_id given.")
  if not PRESET_STEM.match(preset_id):
    raise HTTPException(status_code=400, detail=f"'{preset_id}' is not a preset name.")
  if not os.path.isfile(named_json(CONFIG_DIR, preset_id)):
    raise HTTPException(status_code=404, detail=f"There is no preset '{preset_id}'.")
  own = _unparseable_or_object(core_config.config_path())
  if own is None:
    # Refused rather than written: the file would have become {"preset_id": ...} alone,
    # and the next start's heal would have rebuilt every other setting from the template.
    raise HTTPException(status_code=409, detail=f"{core_config.config_path()} could not be "
                        "read, so the preset was not applied. Fix the file, then try again.")
  own["preset_id"] = preset_id
  _write_json_object(core_config.config_path(), own)
  _apply_saved_config()
  return {"status": "success", "preset_id": preset_id}

# added double because of dev env rules, I didn't want to bother with modifying the link in there
@app.post("/configs")
@app.post("/configs/")
def add_config():
  global SETUP_KEYS
  with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as template_file:
    template = json.load(template_file)
  seed = {k: v for k, v in template.items() if k not in SETUP_KEYS}

  def build(number):
    return {**seed, "config_name": f"Config {number}"}

  config_id, name = create_preset_file(build)
  config_dict = {"id": config_id, "name": name}
  add_config_to_global_list(config_dict)
  return {"status": "success", "config": config_dict}

COPY_SUFFIX = " (Copy)"


def copy_name(source_name):
  """The name a duplicate gets: the source's, with one " (Copy)" -- never two."""
  base = source_name or "Config"
  while base.endswith(COPY_SUFFIX):
    base = base[:-len(COPY_SUFFIX)]
  return base + COPY_SUFFIX


@app.post("/configs/{name}/duplicate")
def duplicate_named_config(name: str):
  source = named_json(CONFIG_DIR, name)
  if not os.path.isfile(source):
    return {"status": "fail"}
  with open(source, "r", encoding="utf-8") as old_file:
    loaded_config = json.load(old_file)
  # Written into the copy as well as the list. The list is rebuilt from each file's
  # config_name at startup, so a copy that kept its source's name in the file came back
  # from a restart indistinguishable from the original.
  loaded_config["config_name"] = copy_name(loaded_config.get("config_name") or name)
  config_id, copied_name = create_preset_file(lambda number: loaded_config)
  config_dict = {"id": config_id, "name": copied_name}
  add_config_to_global_list(config_dict)
  return {"status": "success", "config": config_dict}

@app.get("/configs/{name}")
def get_named_config(name: str):
  path = named_json(CONFIG_DIR, name)
  if os.path.isfile(path):
    with open(path, "r", encoding="utf-8") as old_file:
      loaded_config = json.load(old_file)
      return {
        "status": "success",
        "config": {
          "id": name,
          "name": loaded_config.get("config_name", name),
          "config": loaded_config
        }
      }
  return {"status": "fail"}

@app.put("/configs/{name}")
def update_named_config(name: str, new_config: dict):
  global CURRENT_CONFIGS
  path = named_json(CONFIG_DIR, name)

  # Merged for the same reason POST /config is: the UI only round-trips settings it
  # renders, so writing its state verbatim strips whole blocks out of the preset. A
  # preset that lost them then carries the gap back into config.json the next time it
  # is applied, which is how a configured borrow card came back empty.
  existing = _unparseable_or_object(path)
  if existing is None:
    # A preset nobody can parse is refused, not replaced by the page's copy merged into
    # nothing -- which then went on to every instance applying it.
    raise HTTPException(status_code=409, detail=f"The preset '{name}' could not be read, so "
                        "it was not saved. Fix the file, then save again.")
  merged = merge_into_existing(existing, new_config)

  # Atomic: a preset is read by every instance that applies it, and the save below
  # writes it through to their config files straight afterwards.
  _write_json_object(path, merged)
  # Updated in place. Assigning a new dict to the loop variable, which this used to do,
  # changed nothing in the list: the file took the new name and GET /configs kept serving
  # the old one until a restart.
  for cfg in CURRENT_CONFIGS:
    if cfg["id"] == name:
      cfg["name"] = merged.get("config_name", name)
  updated = _write_preset_through(name, merged)
  return {"status": "success", "applied_to": updated}


def _instance_config_files():
  """Every config an instance runs on: config.json, then each named instance's."""
  files = [("", core_config.CONFIG_PATH)]
  try:
    for path in sorted(Path(core_config.INSTANCE_DIR).glob("*.json")):
      files.append((path.stem, str(path)))
  except OSError:
    pass
  return files


def _write_preset_through(preset_id, preset):
  """Carry a saved preset into every instance that has it applied. Returns their names.

  A preset is shared settings, not a template copied once: two instances on one preset
  are meant to run the same settings. Before this, saving a preset reached only the
  instance whose page did the saving, and every other instance on it went on running
  the old settings while its own page -- which shows the preset -- displayed the new
  ones. The page and the bot disagreeing is the one outcome worse than either answer.

  Written to disk for all of them, then each running one is asked to reload. The keys
  that belong to an emulator rather than to the settings (the Setup page's) are never
  carried: a preset has none, and an instance's device must survive any preset.
  """
  settings = {key: value for key, value in preset.items() if key not in SETUP_KEYS}
  updated = []
  for instance, path in _instance_config_files():
    own = _read_json_object(path)
    if not own or own.get("preset_id") != preset_id:
      continue
    _write_json_object(path, merge_into_existing(own, settings))
    updated.append(instance or "default")

  if not updated:
    return updated
  own_name = bot.instance_name or "default"
  if own_name in updated:
    _apply_saved_config()
  _tell_instances_to_reload([name for name in updated if name != own_name])
  return updated


def _tell_instances_to_reload(names):
  """Ask each running instance in `names` to re-read its config. Best effort."""
  if not names:
    return
  import urllib.request
  for row in live_instances()["instances"]:
    name = row["name"] if row.get("declared") else "default"
    if row.get("current") or not row.get("running") or name not in names or not row.get("port"):
      continue
    try:
      request = urllib.request.Request(f"http://127.0.0.1:{row['port']}/config/reload",
                                       data=b"", method="POST")
      urllib.request.urlopen(request, timeout=2).close()
    except Exception:                                              # noqa: BLE001
      # It is written to disk either way; a process that missed the nudge picks it up
      # when its bot next starts, which reloads the config anyway.
      pass


@app.post("/config/reload")
def reload_config_route():
  """Re-read this instance's config from disk. Sent by another instance's preset save."""
  _apply_saved_config()
  return {"status": "success"}

@app.delete("/configs/{name}")
def remove_named_config(name: str):
  global CURRENT_CONFIGS
  file_path = named_json(CONFIG_DIR, name)
  # The seed. Every new preset is copied from it, POST /configs opens it by name, and
  # config.template.json ships preset_id="default" -- so deleting it 500s the create
  # route and strands the write-through gate (appliedPresetId never matches again, and
  # the bot's own config quietly stops being updated).
  if name == "default":
    raise HTTPException(status_code=400,
                        detail="'default' is the preset every new one is copied from and "
                               "cannot be deleted. Rename it instead.")
  exists = False
  for cfg in CURRENT_CONFIGS:
    if cfg["id"] == name:
      CURRENT_CONFIGS.remove(cfg)
      exists=True
      break
  if exists:
    Path(file_path).unlink()
  #either file exists and we deleted it or it doesn't exist, so we always return success
  return {"status": "success"}

def _schedule(path=None):
  """A schedule file, or an empty one. The source of truth for both of the below.

  Takes a path because the Overview reads other instances' files as well as its own, and
  those are just JSON on the same disk. Nothing is fetched from another process.
  """
  try:
    document = json.loads(Path(path or schedule_path()).read_text(encoding="utf-8"))
    return document if isinstance(document, dict) else {}
  except (OSError, ValueError):
    # No schedule yet is the normal state of a bot that has never deferred anything.
    return {}


def _hold(document=None):
  """The queue-wide hold, if one is running. What a session conflict sets."""
  hold = (_schedule() if document is None else document).get("hold") or {}
  seconds = float(hold.get("until", 0.0) or 0.0) - time.time()
  if seconds <= 0:
    return None
  return {"seconds": round(seconds), "reason": hold.get("reason", "") or ""}


def _queue(document=None, live=True):
  """The task queue in priority order: what each task is and when it next runs.

  Assembled from two sources because neither is complete on its own. The order and the
  full set of names come from the bot, registered at startup -- the schedule file only
  ever holds tasks that have been deferred, so a task that has never waited for anything
  is simply absent from it. The cooldowns come from the file, which is the source of
  truth and is also what makes this readable when the bot is not running at all.
  """
  held = (_schedule() if document is None else document).get("tasks", {}) or {}

  # `live` is false for another instance's row. Only this process knows its own task
  # order, which of them are switched off, and which one it is inside right now -- all
  # three live in memory here, not in the file. So a remote row is built from the
  # cooldowns alone, which is what that file honestly contains: the tasks that have
  # waited for something, and when they next run.
  describe = BOT_CONTROL.get("tasks") if live else None
  order = list(describe()) if describe else [{"name": n, "enabled": True} for n in held]
  running, running_for = entered_task() if live else (None, 0.0)
  now = time.time()

  queue = []
  for task in order:
    name = task["name"]
    entry = held.get(name, {})
    next_run = float(entry.get("next_run", 0.0) or 0.0)
    seconds = max(0.0, next_run - now)
    if not task.get("enabled", True):
      # Reported rather than hidden. A switched-off task listed as "due forever" was the
      # opposite of useful, but leaving it out entirely answers "why is it not collecting
      # missions" with silence; "off" answers it in a word.
      state = "off"
      seconds = 0.0
    elif seconds > 0:
      state = "waiting"
    elif live and name == running and bool(getattr(bot, "is_bot_running", False)):
      state = "running"
    else:
      state = "due"
    queue.append({
      "name": name,
      "state": state,
      "seconds": round(seconds),
      # Only while it is actually waiting. The reason belongs to the cooldown, and
      # nothing runs when one expires to clear it, so the string outlives the wait it
      # explains -- a career deferred once by a debug switch went on saying "the debug
      # switch is pretending TP is short" after a restart with no such switch set, which
      # reads as a bot still doing something it stopped doing days ago.
      "reason": (entry.get("reason", "") or "") if state == "waiting" else "",
      "running_for": round(running_for) if state == "running" else 0,
    })
  return queue


def _instances(stats_dir="stats"):
  """One row per emulator: this one live, the others read off their state files.

  There is no supervisor and no talking between processes. Every instance writes its
  queue to `stats/<device>/schedule.json` and its claim to `owner.json`, and those are
  files on one disk -- so an Overview is a directory listing plus two reads, and it
  degrades exactly as it should: an instance that is not running has a stale file, which
  is precisely what an Overview should show.

  `local` is the important field. Control actions -- run-now, clear-hold -- act on *this*
  process's scheduler, so offering them on another instance's row would quietly drive the
  wrong emulator. The UI gates its buttons on this rather than on which row is first.
  """
  mine = device_key()
  rows = [{
    "name": getattr(bot, "device_id", None) if getattr(bot, "use_adb", False)
            else "desktop",
    "device": mine,
    "local": True,
    "running": bool(getattr(bot, "is_bot_running", False)),
    # A hold outranks the per-task states: while it runs, nothing in the queue can go,
    # whatever the rows say about themselves.
    "hold": _hold(),
    "queue": _queue(),
  }]

  try:
    names = sorted(os.listdir(stats_dir))
  except OSError:
    names = []
  for name in names:
    directory = os.path.join(stats_dir, name)
    if name == mine or not os.path.isdir(directory):
      continue
    schedule = os.path.join(directory, "schedule.json")
    owner = owner_of(directory)
    if not os.path.isfile(schedule) and owner is None:
      # A directory with neither a queue nor a claim is not an instance -- most likely
      # a stats folder for a device that was renamed or retired.
      continue
    document = _schedule(schedule)
    rows.append({
      "name": (owner or {}).get("instance") or name,
      "device": name,
      "local": False,
      # None where liveness cannot be established, which the UI shows as unknown rather
      # than guessing "stopped" at an instance that is running perfectly well.
      "running": (owner or {}).get("alive"),
      "hold": _hold(document),
      "queue": _queue(document, live=False),
    })
  return rows


@app.get("/bot/status")
def bot_status():
  """What the UI needs to render a start/stop control and the queue."""
  return {
    "running": bool(getattr(bot, "is_bot_running", False)),
    "stop_after_career": bool(getattr(bot, "stop_after_career", False)),
    "hotkey": getattr(bot, "hotkey", "f1"),
    "instance": getattr(bot, "instance", 1),
    "device": getattr(bot, "device_id", None) if getattr(bot, "use_adb", False) else None,
    "instances": _instances(),
  }


@app.post("/bot/start")
def bot_start():
  if getattr(bot, "is_bot_running", False):
    return {"status": "already running"}
  _control("start")()
  return {"status": "started"}


@app.post("/bot/stop")
def bot_stop():
  if not getattr(bot, "is_bot_running", False):
    return {"status": "already stopped"}
  _control("stop")()
  return {"status": "stopping"}


@app.post("/bot/stop-after-career")
def bot_stop_after_career():
  """Arm or cancel finishing the career in progress and then stopping."""
  if not getattr(bot, "is_bot_running", False):
    raise HTTPException(status_code=409, detail="Nothing is running.")
  _control("stop_after_career")()
  return {"status": "armed" if getattr(bot, "stop_after_career", False) else "cancelled"}


@app.post("/hold/clear")
def hold_clear():
  """Lift a queue-wide hold now, rather than waiting it out.

  Written straight to the schedule file, the same way run-now is, because the bot is
  blocked in its own thread while a hold runs and the file is what it re-reads. It checks
  once a chunk, so this takes effect within about fifteen seconds.

  Worth being able to do: the usual reason for a session hold is somebody else opening the
  game for a minute, and once they have closed it there is nothing left to wait for.
  """
  # Under the scheduler's lock, and read with the retry: the bot writes this file too,
  # and a bot save landing between this read and this write put the hold straight back.
  with SCHEDULE_FILE_LOCK:
    document = read_json_retrying(schedule_path()) or {}
    # Expired counts as nothing held, which is what the Overview already shows for it.
    until = float((document.get("hold") or {}).get("until", 0.0) or 0.0)
    if until <= time.time():
      return {"status": "nothing held"}
    document["hold"] = {}
    _write_json_object(schedule_path(), document)
  return {"status": "cleared"}


@app.post("/task/{name}/run-now")
def task_run_now(name: str):
  """Clear a task's cooldown so the queue picks it up on the next pass.

  Written straight to the schedule file rather than into the running bot: the bot is a
  thread in this process but its Scheduler is built inside the run, and the file is the
  source of truth for exactly this reason. Scheduler.reload_if_changed picks it up.
  """
  path = schedule_path()
  # See hold_clear: the lock keeps a bot save from landing between this read and write,
  # and the retrying read keeps the bot's own swap from reading as "no schedule".
  with SCHEDULE_FILE_LOCK:
    document = read_json_retrying(path)
    if document is None:
      raise HTTPException(status_code=404, detail="No schedule has been written yet.")
    tasks = document.get("tasks", {})
    if name not in tasks:
      raise HTTPException(status_code=404, detail=f"No task called {name!r} is held.")
    tasks[name]["next_run"] = 0.0
    tasks[name]["reason"] = "run now, from the web UI"
    _write_json_object(path, document)
  return {"status": "due", "task": name}


@app.get("/version.txt")
def get_version():
  # The boot-stamped version, not a re-read of version.txt: after an in-place update the
  # file on disk says the new number while this process still runs the old code, and the
  # sidebar must keep saying what is actually running until it is restarted.
  return PlainTextResponse(BOOT_VERSION)


@app.get("/update/status")
def get_update_status(refresh: bool = False):
  """Whether a newer build is published, and what changed.

  A GET on purpose: this only reads. Nothing here pulls, and nothing here writes to the
  checkout -- the answer is for the user to act on, or ignore.

  With the check switched off the answer is what this build is, and nothing about the
  remote, unless `refresh` asks -- which is the "check now" button, an explicit request
  that overrides a setting meaning "do not ask on your own".
  """
  if not getattr(core_config, "AUTO_CHECK_UPDATES", True) and not refresh:
    return {"current": BOOT_VERSION, "commit": core_version.commit(),
            "latest": "", "behind": False, "notes": "", "url": updates.RELEASE_URL,
            "checked_at": 0.0, "error": "", "enabled": False}
  # The remote side (latest/notes/url) is recomputed as usual; the "current" side is the
  # boot stamp, so a stale process keeps advertising the version it actually runs rather
  # than whatever version.txt now says on disk -- which is what made an old process's
  # banner disappear after an update it had not restarted for.
  answer = dict(updates.status(refresh=refresh))
  answer["current"] = BOOT_VERSION
  answer["behind"] = core_version.is_newer(answer.get("latest", ""), BOOT_VERSION)
  answer["enabled"] = getattr(core_config, "AUTO_CHECK_UPDATES", True)
  return answer


@app.get("/update/preflight")
def get_update_preflight():
  """What would stop an update, without starting one.

  The button asks this before offering itself, so the reason is on screen before anyone
  presses anything -- rather than pressing Update and being told no.
  """
  from core import updater
  refusals = updater.preflight(other_instances=_other_instances())
  previous = updater.last_version()
  return {
    "ready": not refusals,
    "refusals": [refusal.as_dict() for refusal in refusals],
    "branch": updater.current_branch() if updater.is_checkout() else "",
    "commit": updater.head_sha()[:8] if updater.is_checkout() else "",
    "checkout": updater.is_checkout(),
    "releases_url": updates.RELEASE_URL,
    "can_roll_back": bool(previous),
    "rollback_to": previous.get("version") if previous else "",
  }


@app.post("/update/apply")
def post_update_apply():
  """Install the newer build. Only ever from the button; nothing calls this on its own.

  Runs in the request rather than on a thread: an update takes seconds, and the one thing
  that must not happen is the page moving on while the working tree is halfway changed.
  The log is returned whole, and also left in logs/update.log.
  """
  from core import updater
  progress = updater.Progress()
  try:
    return updater.apply(progress=progress, other_instances=_other_instances())
  except updater.Refusal as refusal:
    raise HTTPException(status_code=409,
                        detail={"message": refusal.message, **refusal.as_dict(),
                                "log": progress.text()})


@app.post("/update/rollback")
def post_update_rollback():
  """Go back to the version recorded before the last update."""
  from core import updater
  progress = updater.Progress()
  try:
    return updater.rollback(progress=progress, other_instances=_other_instances())
  except updater.Refusal as refusal:
    raise HTTPException(status_code=409,
                        detail={"message": refusal.message, **refusal.as_dict(),
                                "log": progress.text()})


@app.get("/update/log")
def get_update_log(lines: int = 200):
  """The end of logs/update.log, for a dialog reopened after the fact."""
  from core import instances
  text = instances.tail(os.path.join("logs", "update.log"), lines)
  return {"exists": text is not None, "text": text or ""}


@app.get("/notifs")
def get_notifs():
  folder = "assets/notifications"
  return os.listdir(folder)

# The borrow-card library. Cards are matched by the title the game prints, so this list
# is the schema of record and the images beside it are only what the picker shows --
# adding a card is a line of JSON, with or without artwork.
BORROW_DIR = os.path.join("assets", "independent", "borrow")


@app.get("/borrow/cards")
def list_borrow_cards():
  """Every card in the library, with the title the config stores it under."""
  from core.independent_borrow import load_library
  cards = []
  for card in load_library():
    image = str(card.get("image") or "")
    cards.append({
        "title": card.get("title"),
        "character": card.get("character") or "",
        # Shown beside each card and searchable in the picker; never matched on.
        "rarity": card.get("rarity") or "",
        "type": card.get("type") or "",
        # Only offered when the file is actually there, so the picker can show a
        # placeholder rather than a broken image for a card added without artwork.
        "file": image if image and os.path.isfile(os.path.join(BORROW_DIR, image)) else "",
    })
  return {"cards": sorted(cards, key=lambda c: (c["title"] or "").lower())}


@app.get("/borrow/image/{name}")
def get_borrow_image(name: str):
  """One card image, for the picker's thumbnails.

  Only a bare filename inside the borrow folder is served: the name is reduced to its
  basename and the resolved path is checked to be inside the folder, so a crafted name
  cannot walk out of it.
  """
  safe = os.path.basename(name)
  path = os.path.abspath(os.path.join(BORROW_DIR, safe))
  if not path.startswith(os.path.abspath(BORROW_DIR) + os.sep) or not os.path.isfile(path):
    raise HTTPException(status_code=404, detail="No such borrow card.")
  # Scraped thumbnails are webp; the first crops were png. Both are served as what they are.
  media_type = "image/webp" if safe.lower().endswith(".webp") else "image/png"
  return FileResponse(path, media_type=media_type)


@app.get("/data/{path:path}")
async def get_data_file(path: str):
  file_path = inside(DATA_DIR, path)
  if not os.path.isfile(file_path):
    raise HTTPException(status_code=404)
  return FileResponse(file_path, headers={
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0"
  })

PATH = "web/dist"

@app.get("/")
async def root_index():
  return FileResponse(os.path.join(PATH, "index.html"), headers={
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0"
  })

@app.get("/{path:path}")
async def fallback(path: str):
  file_path = inside(WEB_DIR, path)
  if not os.path.isfile(file_path):
    raise HTTPException(status_code=404)
  headers = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0"
  }

  media_type = "application/javascript" if str(file_path).endswith((".js", ".mjs")) else None
  return FileResponse(file_path, media_type=media_type, headers=headers)
