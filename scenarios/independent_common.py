"""The handful of things every Independent Training handler needs.

Small on purpose. This exists so a task can live in its own module without importing
`independent_training` and making a cycle of it: the task modules import from here, and
`independent_training` imports the task modules to build its handler table.

Anything that belongs to one task goes in that task's module; anything a screen handler
touches by reflex -- tap a template, tap a point, read a cell -- goes here.
"""
import re

import utils.constants as constants
import utils.device_action_wrapper as device_action
from core.ocr import extract_text
from utils.log import args, info
from utils.screenshot import enhanced_screenshot
from utils.tools import get_secs

# The names the scheduler knows each task by. Here rather than beside the tasks
# themselves because `build_tasks` names all five and each module names its own, and a
# task's name being a string two files apart agreed on is how one of them ends up
# deferring a task nobody ever runs.
TASK_TEAM_TRIALS = "team_trials"
TASK_MISSIONS = "missions"
TASK_PRESENT_BOX = "present_box"
TASK_CAREER = "career"
TASK_DAILY_RACES = "daily_races"


def _dry_run():
  return bool(getattr(args, "dry_run_turn", False))


def _click(template, min_search=2.0, region=None):
  """Locate and click a template inside the game window."""
  if _dry_run():
    info(f"[dry-run] would click {template}")
    return True
  return device_action.locate_and_click(
    template,
    min_search_time=get_secs(min_search),
    region_ltrb=region or constants.GAME_WINDOW_BBOX,
  )


def _click_point(x, y, text=""):
  """Click a fixed point inside the game window.

  Used where there is nothing distinctive to match on -- the Training Focus radios are
  three identical grey circles, so only their position tells them apart.
  """
  if _dry_run():
    info(f"[dry-run] would click ({x}, {y}) -- {text}")
    return True
  device_action.click(target=(x, y), text=text)
  return True


def _ocr(region_xywh, allowlist=None):
  # Every caller of this crops one cell holding one line, so it takes the recognise-only
  # path -- see core/ocr, where detection is the default precisely so that a reader which
  # has not been thought about is slow rather than wrong.
  image = enhanced_screenshot(region_xywh)
  return (extract_text(image, use_recognize=True, allowlist=allowlist) if allowlist
          else extract_text(image, use_recognize=True))


def _read_int(region_xywh, allowlist="0123456789"):
  """An integer from `region_xywh`, or None. Grouping commas are stripped.

  Here rather than beside any one caller: four modules read a number off a cell, and a
  second copy of this would be a second answer to "what counts as unreadable".
  """
  digits = re.sub(r"[^0-9]", "", _ocr(region_xywh, allowlist=allowlist) or "")
  return int(digits) if digits else None
