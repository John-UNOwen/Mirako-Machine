"""Instrument one skill-survey cycle stage by stage on the live screen."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import core.bot as bot
import core.independent_skill as skill
import utils.adb_actions as adb_actions
import utils.constants as constants
import utils.device_action_wrapper as device_action
from adbutils import adb
from utils.log import debug

bot.use_adb = True
bot.is_bot_running = True
device_action.check_if_connecting = lambda: None
constants.adjust_constants_x_coords(offset=-155)
adb_actions.device = adb.device("127.0.0.1:5555")

for cycle in range(3):
    marks = {}

    t0 = time.time()
    device_action.flush_screenshot_cache()
    before = skill._capture_scroll_region()
    marks["capture"] = time.time() - t0

    t0 = time.time()
    rows = skill.parse_skill_rows(before)
    marks["parse"] = time.time() - t0

    t0 = time.time()
    skill._scroll("down")
    marks["scroll+settle"] = time.time() - t0

    t0 = time.time()
    device_action.flush_screenshot_cache()
    after = skill._capture_scroll_region()
    marks["end-capture"] = time.time() - t0

    t0 = time.time()
    same = skill._regions_identical(before, after)
    marks["compare"] = time.time() - t0

    total = sum(marks.values())
    detail = "  ".join(f"{k}={v:.2f}s" for k, v in marks.items())
    print(f"cycle {cycle}: total {total:.2f}s  {detail}  rows={len(rows)} end={same}")
    if same:
        break
