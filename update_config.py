import json
import os

from core.atomic_write import write_json_atomic

TEMPLATE_FILE = "config.template.json"
CONFIG_FILE = "config.json"
# Where the Setup page saves the default instance's settings. A named instance's page
# writes its own file as well, but the default instance's save used to stop here -- a
# file its process never reads.
SETUP_FILE = os.path.join("config", "setup.json")
is_changed = False

SETUP_KEYS = [
  "sleep_time_multiplier",
  "use_adb",
  "window_name",
  "device_id",
  "ocr_use_gpu",
  "auto_check_updates",
  "notifications_enabled",
  "error_notification",
  "success_notification",
  "notification_volume",
  "preset_id"
]

INSTANCE_DIR = os.path.join("config", "instances")

# Settings of the turn-by-turn career, which this build does not have. Nothing reads any
# of them, and until 1.0.6 the template still carried all of them -- so every config,
# preset and instance file grew them back at each startup, and they were most of every
# file a user opened.
#
# Named, rather than pruned as "anything the template does not have": the merge below
# keeps a key it does not recognise on purpose -- one added by hand, or one written by a
# newer build that a rollback has left behind -- and a general prune would delete those
# along with these. These are known to be dead, so exactly these go.
RETIRED_KEYS = (
  "priority_stat", "priority_weights", "priority_weight", "stat_caps",
  "skip_training_energy", "never_rest_energy", "skip_infirmary_unless_missing_energy",
  "hint_hunting_enabled", "hint_hunting_weights", "stop_at_turns",
  "use_skip_claw_machine", "wit_training_score_ratio_threshold",
  "rainbow_support_weight_addition", "non_max_support_weight",
  "scenario_gimmick_weight", "race_turn_threshold", "do_mission_races_if_possible",
  "prioritize_missions_over_g1", "minimum_condition_severity", "minimum_mood",
  "minimum_mood_junior_year", "maximum_failure", "minimum_aptitudes",
  "rest_before_summer_energy", "use_race_schedule", "cancel_consecutive_race",
  "position_selection_enabled", "enable_positions_by_race", "preferred_position",
  "positions_by_race", "race_schedule", "function_fallbacks",
  "minimum_acceptable_scores", "event", "training_strategy",
  # Never played by anything: sounds only ever mark a session stopping, as a success
  # or an error. Its picker said "currently unused" in the build this was forked from.
  "info_notification",
)
# Sub-keys of blocks that are otherwise live. The career checked its skill points
# between turns; Independent Training buys once, on the Learn screen.
RETIRED_NESTED = {
  "skill": ("skill_check_turns", "check_skill_before_races", "skill_pts_check"),
  # The spark choice briefly offered a server channel beside DMs, then was asked through
  # the player's own Discord bot (2026-09-25). The shared Mirako bot replaced both.
  "webhook": ("choice_target", "choice_channel_id", "bot_token", "choice_user_id"),
}


def drop_retired_keys(config_dict):
  """Remove the retired settings from `config_dict` in place; return what went."""
  removed = [key for key in RETIRED_KEYS if key in config_dict]
  for key in removed:
    del config_dict[key]
  for block, subkeys in RETIRED_NESTED.items():
    inner = config_dict.get(block)
    if not isinstance(inner, dict):
      continue
    for subkey in subkeys:
      if subkey in inner:
        del inner[subkey]
        removed.append(f"{block}.{subkey}")
  return removed


def is_whole_config(file_path):
  """Whether `file_path` is a config a process runs on, rather than a preset.

  Presets under config/ deliberately carry no setup keys -- the device, the window, the
  notification sounds live beside them in setup.json -- so this module strips those keys
  from every file that is not config.json. An instance file is not a preset: it is the
  whole config one emulator runs on, the same as config.json is for the default instance.
  Treated as a preset, it lost its device_id at every startup and every instance fell
  back to the template's -- the first emulator's -- saved only by --use-adb winning at
  runtime.
  """
  normal = os.path.normcase(os.path.normpath(file_path))
  if normal == os.path.normcase(os.path.normpath(CONFIG_FILE)):
    return True
  return (os.path.normcase(os.path.dirname(os.path.abspath(file_path)))
          == os.path.normcase(os.path.abspath(INSTANCE_DIR)))


def is_default_config(file_path):
  """Whether `file_path` is the default instance's config.json, not a named instance's.

  Only the default instance can carry a Setup key its process never read: a named
  instance's file is the whole config it runs on, and the page's save writes there.
  """
  return (os.path.normcase(os.path.normpath(file_path))
          == os.path.normcase(os.path.normpath(CONFIG_FILE)))


def _read_json(path):
  try:
    with open(path, "r", encoding="utf-8") as handle:
      return json.load(handle)
  except (OSError, ValueError):
    return None


def _same_json_type(value, default):
  """Whether two values are the same kind of thing in the JSON the page writes.

  Python's bool is an int, so without this 0 compares through as the template's
  False and a hand-edited 0 reads as a setting the page saved. And a string where
  the template has a switch is not a switch -- it would be adopted and run, or
  crash on, if the heal trusted values over types.
  """
  if isinstance(value, bool) or isinstance(default, bool):
    return isinstance(value, bool) and isinstance(default, bool)
  if isinstance(value, (int, float)) or isinstance(default, (int, float)):
    return isinstance(value, (int, float)) and isinstance(default, (int, float))
  return type(value) is type(default)


def heal_setup_keys(config_dict, template):
  """Recover the Setup page's saved values the default instance's process never read.

  The default instance's Setup save used to reach `setup.json` alone, which
  load_config never reads -- so a saved switch (the ADB one above all) sat in a file
  nothing ran on while config.json kept the template's value, and the page and the
  running bot disagreed with nothing saying so. Where the file still carries the
  template's value and setup.json says otherwise, the saved value is adopted: it is
  the last thing the user chose. A value the file moved away from is a deliberate
  one, and the heal leaves it.
  """
  global is_changed
  setup = _read_json(SETUP_FILE)
  if not isinstance(setup, dict):
    # A file the page could not have written. Ignored, not fatal: this runs before
    # anything else at start, and a non-dict here used to end the process with a
    # traceback and no other way in.
    return
  for key in SETUP_KEYS:
    default = template.get(key)
    saved = setup.get(key)
    if saved is None or not _same_json_type(saved, default):
      if saved is not None:
        print(f"Skipping '{key}' from {SETUP_FILE}: {saved!r} is not the type the "
              f"template's {default!r} has, so it is not a setting the page could "
              f"have saved.")
      continue
    # Nothing to recover when the saved value is the template's own. Without this every
    # such key was "healed" into the value it already had, announced once per key on
    # every start and rewriting the file each time -- ten lines of noise for the one key
    # the page had really changed.
    if saved == default:
      continue
    # The type check is the other half of "still at the default": 0 == False in
    # Python, so a hand-edited 0 would read as the template's False without it.
    if config_dict.get(key) != default or not _same_json_type(config_dict.get(key),
                                                              default):
      continue
    config_dict[key] = saved
    is_changed = True
    print(f"Healing '{key}' from {SETUP_FILE}: the Setup page saved it there, where "
          f"the default instance never reads it, and the file still carried the "
          f"template's {default!r}.")


# one level deep merge on only whitelisted keys
NESTED_SHALLOW_KEYS = ["skill","independent_training"]

def update_config(file_path=None):
  global is_changed, NESTED_SHALLOW_KEYS, TEMPLATE_FILE, CONFIG_FILE
  is_changed = False
  if not file_path:
    file_path = CONFIG_FILE

  if not os.path.exists(TEMPLATE_FILE):
    raise FileNotFoundError(f"Missing template file: {TEMPLATE_FILE}")

  # Load template
  with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
    template = json.load(f)

  # If config doesn't exist, create it exactly from template
  if not os.path.exists(file_path):
    print(f"{file_path} not found. Creating a new one from template...")
    created = dict(template)
    # A setup.json from a life the file did not live: its saved device is the one the
    # page still shows, so the new file runs on it rather than the template's.
    if is_default_config(file_path):
      heal_setup_keys(created, template)
    write_json_atomic(file_path, created)
    return created

  # Load user config
  with open(file_path, "r", encoding="utf-8") as f:
    user_config = json.load(f)

  # Before the merge, which would otherwise keep them as keys it does not recognise.
  retired = drop_retired_keys(user_config)
  if retired:
    is_changed = True
    print(f"Removing {len(retired)} settings from {file_path} that only the retired "
          f"career mode used.")

  # Apply shallow merge (only top-level keys)
  updated = shallow_merge(template, user_config, file_path)

  for k in NESTED_SHALLOW_KEYS:
    updated = shallow_merge_key(k, template, updated)

  # A file that never moved off the template still carries template values for the
  # keys the Setup page saved to setup.json, which this file is the default
  # instance's and its process runs on.
  if is_default_config(file_path):
    heal_setup_keys(updated, template)

  # Save only if something changed
  if is_changed:
    print(f"Saving updated {file_path}...")
    # Atomic: this runs at startup against the live config, and a kill mid-write left a
    # truncated config.json that the instance could not boot from at all.
    write_json_atomic(file_path, updated)

  return updated

def shallow_merge(template: dict, user_config: dict, file_path: str) -> dict:
  global is_changed, SETUP_KEYS

  final = {}
  whole = is_whole_config(file_path)

  # This has become complicated, should look into separating it into two functions maybe.
  for key, t_val in template.items():
    if key in user_config:
      if whole:
        final[key] = user_config[key]
      else:
        if key in SETUP_KEYS:
          is_changed = True
          print(f"Removing top-level key: {key}")
        else:
          final[key] = user_config[key]
    else:
      if whole:
        is_changed = True
        print(f"Adding missing top-level key: {key}")
        final[key] = t_val
      else:
        if key not in SETUP_KEYS:
          is_changed = True
          print(f"Adding missing top-level key: {key}")
          final[key] = t_val

  # Add any user-defined extra keys at the end, preserving their order
  for key, u_val in user_config.items():
    if key not in template:
      final[key] = u_val

  return final

def shallow_merge_key(key: str, template: dict, user_config: dict) -> dict:
  global is_changed

  if key not in template:
    return user_config

  t_val = template[key]

  if key not in user_config:
    print(f"Adding missing top-level key (via shallow_merge_key): {key}")
    user_config[key] = t_val
    is_changed = True
    return user_config

  u_val = user_config[key]

  if not isinstance(t_val, dict) or not isinstance(u_val, dict):
    return user_config

  for subkey, t_subval in t_val.items():
    if subkey not in u_val:
      print(f"Adding missing nested key: {key}.{subkey}")
      u_val[subkey] = t_subval
      is_changed = True

  return user_config
