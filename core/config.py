import json
import os

import core.bot as bot

CONFIG_PATH = "config.json"
TEMPLATE_PATH = "config.template.json"
INSTANCE_DIR = os.path.join("config", "instances")
MACHINE_PATH = os.path.join("config", "machine.json")

# Settings that belong to the box rather than to an emulator, so machine.json owns them
# and an instance file saying otherwise is reported and overruled. Named here rather than
# enforced by stripping: an instance file that still carries one is not broken, it is just
# no longer the thing being read, and saying so is more useful than silently editing it.
MACHINE_OWNED = ("sleep_time_multiplier", "ocr_use_gpu", "notifications_enabled",
                 "error_notification", "success_notification",
                 "notification_volume", "webhook")

# Where the last load found the instance file and machine.json disagreeing. Kept as data
# rather than logged from here, because core.config is imported by everything and must not
# start a logging import cycle to say so; main.py reports it once at startup.
MACHINE_CONFLICTS = []

#put a default for sleep time multiplier since it's an important value
SLEEP_TIME_MULTIPLIER = 1

# Defaults only. reload_config() overwrites both from the "webhook" block, which is
# where the web UI edits them -- before that landed these were module constants and the
# URL could only be set by editing this file.
WEBHOOK_URL = ""

def config_path():
  """The config file this process reads.

  `config.json` unless an instance was declared, in which case its own file under
  `config/instances/`. Resolved on every call rather than captured at import: the name
  arrives on the command line after these modules are imported, and a constant read at
  import time is exactly how the Discord marker spent its life insisting it was
  instance 1.
  """
  if bot.instance_name:
    return os.path.join(INSTANCE_DIR, f"{bot.instance_name}.json")
  return CONFIG_PATH


def _read_json(path):
  try:
    with open(path, "r", encoding="utf-8") as handle:
      return json.load(handle)
  except (OSError, ValueError):
    return None


def _merge(base, overlay):
  """`overlay` laid over `base`, a dict at a time. A list replaces rather than extends."""
  merged = dict(base)
  for key, value in overlay.items():
    if isinstance(value, dict) and isinstance(merged.get(key), dict):
      merged[key] = _merge(merged[key], value)
    else:
      merged[key] = value
  return merged


def _disagreements(own, machine, prefix=""):
  """Paths where machine.json overrides a value the instance file states differently."""
  found = []
  for key, value in machine.items():
    here = f"{prefix}{key}"
    if key not in own:
      continue
    if isinstance(value, dict) and isinstance(own[key], dict):
      found.extend(_disagreements(own[key], value, f"{here}."))
    elif own[key] != value:
      found.append(here)
  return found


# to see any config variables you must call reload_config()
def load_config(path=None):
  """The settings this process runs on, in three layers.

  The template first, supplying a default for anything the file does not mention; then
  the file itself; then `config/machine.json`, which owns the settings that belong to the
  box rather than to one emulator.

  The template layer is why a missing key no longer stops the bot. reload_config reads
  with bare subscripts, so an absent one used to raise RuntimeError naming it and end the
  process -- tolerable when there was a single config to keep complete, not once there is
  one per instance and every new key has to reach all of them.
  """
  global MACHINE_CONFLICTS
  path = path or config_path()
  own = _read_json(path)
  if own is None:
    raise RuntimeError(f"Could not read the config at {path}.")
  merged = _merge(_read_json(TEMPLATE_PATH) or {}, own)
  machine = _read_json(MACHINE_PATH)
  MACHINE_CONFLICTS = _disagreements(own, machine) if machine else []
  return _merge(merged, machine) if machine else merged

def load_var(var_name, value):
  globals()[var_name] = value


def _nonneg_int(value):
  """A raw config value as a non-negative int, never raising.

  Non-numeric junk (null, a boolean, a list, a string that does not parse as
  a number) and NaN/inf become 0; a negative becomes 0; a fraction truncates
  (2.7 -> 2); a valid non-negative int passes through unchanged. A string that
  DOES parse is taken as its number -- the consumer's old int() parsed "3"
  as 3, so clamping it to 0 would silently uncap a setting the user wrote as
  a string. Used for INDEPENDENT_MAX_RUNS, whose contract is "0 = no cap":
  after clamping a negative max_runs behaves as 0 = 'no cap' (bool(0) is
  False), so _career_limit_reached's `bool(max_runs) and
  runs_completed >= max_runs` can never mean 'cap reached at run 0' for
  negative or fractional input, and a junk value can no longer raise in the
  consumer's int().
  """
  if isinstance(value, bool) or value is None:
    return 0
  if isinstance(value, str):
    # A count kept as a string is not junk: it used to reach the consumer's
    # int() and be parsed, and a hand-edited "3" must still mean 3, not
    # 0 = no cap. float() accepts "3", "3.0" and "-5" alike; the string's
    # nan/inf are caught by the float guard below, unparseable text here.
    try:
      value = float(value)
    except ValueError:
      return 0
  if isinstance(value, int):
    return max(value, 0)
  if isinstance(value, float):
    # NaN (value != value) and inf would break int(); both are junk for a count.
    if value != value or value in (float("inf"), float("-inf")):
      return 0
    return int(value) if value >= 0 else 0
  return 0

def _borrow_titles(entries):
  """Borrow entries as card titles, translating anything left over from the old format.

  Selection used to store a path to a cropped PNG. It now stores the card's title, and a
  config written by the old picker would otherwise load as a list of paths that match no
  card at all -- an empty selection that still looks populated in the UI. The library
  keeps the image filename beside each title, so an old entry can be translated rather
  than discarded.

  Read with the standard library only, and deliberately not through
  core.independent_borrow: config is imported by nearly everything, and that module
  reaches cv2 and the screen specs.
  """
  library = []
  try:
    with open("data/borrow_cards.json", "r", encoding="utf-8") as file:
      library = json.load(file).get("cards", [])
  except (OSError, ValueError):
    # A missing or unreadable library is not fatal here; titles still load untouched and
    # core.independent_borrow warns about it once, where it matters.
    pass
  by_image = {str(card.get("image", "")).lower(): card.get("title")
              for card in library if card.get("image")}

  titles = []
  for entry in entries:
    if not entry:
      continue
    text = str(entry)
    looks_like_a_path = text.lower().endswith(".png") or "/" in text or "\\" in text
    if looks_like_a_path:
      basename = text.replace("\\", "/").rsplit("/", 1)[-1].lower()
      translated = by_image.get(basename)
      if translated:
        titles.append(translated)
      # An untranslatable path is dropped rather than kept: kept, it would be matched as
      # a title, and "esteemed_and_adored_heirs_to_the_throne.png" normalises close
      # enough to the real title to match it by accident, which is worse than nothing.
      continue
    titles.append(text)
  return titles


def reload_config():
  try:
    config = load_config()

    # The turn-by-turn career's settings used to be loaded here too -- 42 variables,
    # plus a training-strategy expander, all hard-indexed, so a config without them
    # refused to start although nothing read any of them. Retired in 1.0.6; the keys are
    # stripped from existing files by update_config.RETIRED_KEYS.
    load_var('IS_AUTO_BUY_SKILL', config["skill"]["is_auto_buy_skill"])
    load_var('SKILL_LIST', config["skill"]["skill_list"])
    # Skills never to be bought, whatever else asks for them. Family-level: one entry
    # blocks every tier of that skill. The web UI moves a skill between this and
    # skill_list rather than allowing it in both, so a skill in both lists means a
    # hand-edited config -- the buyer warns and treats the veto as the winner.
    load_var('SKILL_BLACKLIST', config["skill"].get("skill_blacklist", []))
    # Discord notifications. The URL is the on/off switch: with none set, nothing is
    # sent and no thread work happens.
    webhook_conf = config.get("webhook", {})
    load_var('WEBHOOK_URL', str(webhook_conf.get("url", "")).strip())
    load_var('WEBHOOK_CAREER_SUMMARY_ENABLED',
             webhook_conf.get("career_summary_enabled", True))
    load_var('WEBHOOK_RECOVERY_ENABLED', webhook_conf.get("recovery_enabled", True))
    load_var('WEBHOOK_SKILLS_ENABLED', webhook_conf.get("skills_enabled", True))
    # The Discord bot the spark choice is asked through. See core/discord_choice.py.
    load_var('WEBHOOK_BOT_TOKEN', str(webhook_conf.get("bot_token", "")).strip())
    load_var('WEBHOOK_CHOICE_CHANNEL_ID',
             str(webhook_conf.get("choice_channel_id", "")).strip())
    # "dm" sends the question to one person's direct messages instead of a channel.
    load_var('WEBHOOK_CHOICE_TARGET', webhook_conf.get("choice_target", "channel"))
    load_var('WEBHOOK_CHOICE_USER_ID', str(webhook_conf.get("choice_user_id", "")).strip())
    load_var('SLEEP_TIME_MULTIPLIER', config["sleep_time_multiplier"])
    load_var('WINDOW_NAME', config["window_name"])
    load_var('CONFIG_NAME', config["config_name"])
    load_var('USE_ADB', config["use_adb"])
    load_var('DEVICE_ID', config["device_id"])
    load_var('OCR_USE_GPU', config["ocr_use_gpu"])
    load_var('AUTO_CHECK_UPDATES', config["auto_check_updates"])
    load_var('NOTIFICATIONS_ENABLED', config["notifications_enabled"])
    load_var('ERROR_NOTIFICATION', config["error_notification"])
    load_var('SUCCESS_NOTIFICATION', config["success_notification"])
    load_var('NOTIFICATION_VOLUME', config["notification_volume"])

    # Read defensively rather than with the hard indexing used above, so a config
    # written before any of these settings existed still starts -- each one falls back
    # to its default instead of the whole bot refusing to load over a missing key.
    independent = config.get("independent_training", {})
    # Clamped on load rather than at the consumer: stored raw, a negative max_runs reads
    # as "cap reached at run 0" (bool(-5) is True) and a junk value raises in the
    # consumer's int(). Clamped, both behave as 0 = 'no cap'. A numeric string is
    # parsed, not zeroed, the way the consumer's old int() parsed it -- a hand-edited
    # "3" still means a cap of three; see _nonneg_int.
    load_var('INDEPENDENT_MAX_RUNS', _nonneg_int(independent.get("max_runs", 0)))
    load_var('INDEPENDENT_AFTER_MAX_RUNS', independent.get("after_max_runs", "stop"))
    # Restarting the game after a stuck run. On by default: the walk from the title
    # screen back to a running career is already built and exercised daily, and career
    # progress is held server-side, so the usual cost of a stuck night is the night
    # rather than the career. Only the stops marked recoverable at their call site
    # restart -- see RESTARTABLE in independent_training, and the pair it excludes.
    # Running out of TP: a wait, or the end of the session. Defaults to waiting -- TP is
    # never wasted by arriving while the bot idles, and an unattended run that stops at
    # 2am for want of six TP is the thing the task queue exists to fix. Off restores the
    # old behaviour exactly.
    # The two daily chores on the home screen. Both are free collection and both are
    # idempotent, so they default on; either can be turned off without touching the other.
    # How long to hold everything off when the account turns up signed in on another
    # device. Not a stop, because the other device may well be someone closing the app in
    # a minute; not a retry either, because logging straight back in is what starts the
    # two swapping each other off. 0 restores the old behaviour: stop and say so.
    load_var('INDEPENDENT_SESSION_CONFLICT_WAIT_MINUTES',
             independent.get("session_conflict_wait_minutes", 60))
    load_var('INDEPENDENT_COLLECT_MISSIONS', independent.get("collect_missions", True))
    load_var('INDEPENDENT_COLLECT_PRESENTS', independent.get("collect_presents", True))
    # Daily races. Six tickets a day, spent in one Multi-Race entry.
    load_var('INDEPENDENT_DAILY_RACES_ENABLED',
             independent.get("daily_races_enabled", False))
    load_var('INDEPENDENT_DAILY_RACE_PROGRAM',
             independent.get("daily_race_program", "moonlight_sho"))
    load_var('INDEPENDENT_DAILY_RACE_DIFFICULTY',
             independent.get("daily_race_difficulty", "very_hard"))
    load_var('INDEPENDENT_DAILY_RACE_TICKETS_PER_DAY',
             independent.get("daily_race_tickets_per_day", 6))
    load_var('INDEPENDENT_WAIT_FOR_TP', independent.get("wait_for_tp", True))
    # Two switches for exercising that wait without an account that is really out of TP
    # and four hours to spare. The first forces the wait path only -- never the refill
    # path -- so testing it cannot spend carats; the second only ever shortens the hold.
    load_var('INDEPENDENT_DEBUG_PRETEND_TP_SHORT',
             independent.get("debug_pretend_tp_short", False))
    load_var('INDEPENDENT_DEBUG_TP_WAIT_SECONDS',
             independent.get("debug_tp_wait_seconds", 0))
    load_var('INDEPENDENT_RESTART_ON_STUCK', independent.get("restart_on_stuck", True))
    load_var('INDEPENDENT_RESTART_MAX_PER_SESSION',
             independent.get("restart_max_per_session", 10))
    # Restarts in a row for the same problem before the guard gives up on it. The
    # guard clamps it to at least one; see same_kind_limit.
    load_var('INDEPENDENT_RESTART_MAX_SAME_KIND',
             independent.get("restart_max_same_kind", 5))
    # Empty means read it off the device. Worth leaving empty: this install is
    # "com.cygames.umamusume" while its own activity is under jp.co.cygames, and the JP
    # build differs again, so a hardcoded default would be wrong for somebody.
    load_var('INDEPENDENT_GAME_PACKAGE', str(independent.get("game_package", "")).strip())
    load_var('INDEPENDENT_WAIT_POLL_SECONDS', independent.get("wait_poll_seconds", 60))
    load_var('INDEPENDENT_TRAINING_MINUTES', independent.get("training_minutes", 50))
    # Borrow Card titles, in priority order -- the bracketed line the Borrow Card list
    # prints for each friend. Max limit break is enforced separately by counting the
    # thumbnail's pips, which is why there is no minimum-limit-break setting. There is no
    # give-up setting either: a career cannot start with an empty friend slot, so the
    # loop refreshes until one of these turns up. A bare string is still accepted.
    #
    # These used to be paths to cropped artwork. Anything that still looks like one is
    # translated through the library rather than dropped, because a config written by the
    # old picker is otherwise silently empty -- which presents as "the bot cannot find my
    # card", the exact failure this replaced.
    borrow_cards = independent.get("borrow_cards",
                                   independent.get("borrow_card_template", []))
    if isinstance(borrow_cards, str):
      borrow_cards = [borrow_cards] if borrow_cards else []
    load_var('INDEPENDENT_BORROW_CARDS', _borrow_titles(borrow_cards))
    load_var('INDEPENDENT_BORROW_WARN_EVERY', independent.get("borrow_warn_every_refreshes", 10))
    # Which sparks a career has to come away with, and when a reroll may be spent on
    # them. Interpreted by core/independent_sparks.py.
    load_var('INDEPENDENT_SPARK_REROLL', independent.get("spark_reroll", {}))

    # Skill points do not survive the end of a career, so any balance left after the
    # priority list is bought is simply lost. With this on, the remainder is spent on
    # skills that are not on the list, taken from the bottom of the game's list upwards.
    load_var('INDEPENDENT_SPEND_LEFTOVER_POINTS',
             independent.get("spend_leftover_points", False))
    # How that leftover is spent. "bottom_up" takes the end of the game's list first,
    # which is the direction the purchase pass already walks. "best_value" takes the most
    # heavily discounted first, buying more for the same points. The priority list is
    # honoured before either of them.
    # "maximize_rating" solves a knapsack over the trainee's aptitudes; the other two
    # are ordering walks. See core/independent_skill.best_by_rating.
    load_var('INDEPENDENT_LEFTOVER_STRATEGY',
             independent.get("leftover_strategy", "bottom_up"))
    # Replaces the leftover walk with a knapsack over the trainee's aptitudes. See
    # core/independent_skill.best_by_rating.
    team_trials = config.get("team_trials", {})
    load_var('TEAM_TRIALS_ENABLED', team_trials.get("enabled", False))
    # RP refills one charge every two hours to a maximum of five; a floor above zero
    # leaves something in hand rather than draining the bar.
    load_var('TEAM_TRIALS_KEEP_CHARGES', team_trials.get("keep_charges", 0))
    # Rare: a "With Every Win!" badge on one of the three opponents pays out per win.
    # On by default -- the badge only ever adds a reward, so the only reason to turn it
    # off is to keep the choice predictable.
    load_var('TEAM_TRIALS_PRIORITISE_REWARD',
             team_trials.get("prioritise_reward", True))

    load_var('INDEPENDENT_MAXIMIZE_RATING',
             independent.get("maximize_rating", False))
    # Training Focus radio on the Final Confirmation screen. "default" leaves whatever
    # the game already has; otherwise it is only clicked when it differs.
    load_var('INDEPENDENT_TRAINING_FOCUS', independent.get("training_focus", "default"))
    # Scenario to select on Scenario Select. "default" presses Next on whatever the game
    # has; anything else turns the carousel until that scenario is showing.
    load_var('INDEPENDENT_SCENARIO', independent.get("scenario", "default"))
    # Support deck to select on Support Formation: 1-10, or 0 to leave whatever is up. A
    # non-empty deck_name overrides the number and is matched against the deck's title.
    load_var('INDEPENDENT_DECK', independent.get("deck", 0))
    load_var('INDEPENDENT_DECK_NAME', independent.get("deck_name", ""))
    # Saved race agenda to load on My Agendas: its position, 1 being the top of the list
    # and what the bot loaded before this setting existed. A non-empty agenda_name wins
    # over the position and picks the first agenda with that name -- names can repeat.
    load_var('INDEPENDENT_AGENDA_SLOT', independent.get("agenda_slot", 1))
    load_var('INDEPENDENT_AGENDA_NAME', str(independent.get("agenda_name", "") or "").strip())
    # Racing style to set on the Final Confirmation screen before starting. "default"
    # leaves whatever the trainee came with and skips the whole dialog.
    load_var('INDEPENDENT_RACING_STYLE', independent.get("racing_style", "default"))
    load_var('INDEPENDENT_TP_REFILL_ENABLED', independent.get("tp_refill_enabled", False))
    # Which item to spend on TP. "toughness_only" never touches carats; "toughness_first"
    # falls back to them once the free items run out; "carats_first" saves the free items
    # for later. Carats are premium currency, so the floor below is honoured either way.
    load_var('INDEPENDENT_TP_REFILL_STRATEGY',
             independent.get("tp_refill_strategy", "toughness_first"))
    load_var('INDEPENDENT_TP_REFILL_MAX_PER_SESSION', independent.get("tp_refill_max_per_session", 0))
    load_var('INDEPENDENT_TP_REFILL_MIN_CARATS', independent.get("tp_refill_min_carats_remaining", 0))
    # Debug switch, equivalent to --select-skills-only: select the skills on the Learn
    # screen and stop before Confirm, so the selection can be inspected and re-run with
    # the game's own Reset. Kept in config rather than argv only so it can be flipped
    # without restarting the process.
    # Debug switch: set the career up completely, then stop instead of pressing Start.
    load_var('INDEPENDENT_DEBUG_STOP_BEFORE_START',
             independent.get("debug_stop_before_start", False))
    load_var('INDEPENDENT_DEBUG_SELECT_SKILLS_ONLY',
             independent.get("debug_select_skills_only", False))
    load_var('INDEPENDENT_DEBUG_FORCE_TP_REFILL',
             independent.get("debug_force_tp_refill", False))
    load_var('INDEPENDENT_DEBUG_STOP_BEFORE_SPARK_REROLL',
             independent.get("debug_stop_before_spark_reroll", False))

  except KeyError as e:
    raise RuntimeError(f"Missing config key: {e.args[0]}, please copy it to config.json from config.template.json and try again")

