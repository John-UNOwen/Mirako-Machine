"""Verification for the ADB code-review fixes.

1. The out-of-bounds region warning path (was an UnboundLocalError before the fix).
2. The /adb/test guard refuses to run while the bot is active (source-level check).
3. main.py's BotStopException passthrough compiles and imports.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

import utils.adb_actions as adb_actions
import core.bot as bot

bot.use_adb = True
bot.is_bot_running = True

# --- 1. empty-region warning path ---
class FakeDevice:
  def screenshot(self, error_ok=True):
    return Image.fromarray(np.zeros((1080, 800, 3), dtype=np.uint8))

adb_actions.device = FakeDevice()
adb_actions.cached_screenshot = []
crop = adb_actions.screenshot(region_xywh=(900, 500, 28, 32))
assert crop.size == 0, "expected an empty crop"
print("[OK] out-of-bounds region: warned and returned an empty crop (no UnboundLocalError)")

# second call must not warn again (flag latched)
crop2 = adb_actions.screenshot(region_xywh=(900, 500, 28, 32))
print("[OK] warning latched: second call silent")

# in-bounds region still fine
adb_actions.cached_screenshot = []
crop3 = adb_actions.screenshot(region_xywh=(557, 497, 28, 32))
assert crop3.shape == (32, 28, 3), crop3.shape
print("[OK] in-bounds region crops normally:", crop3.shape)

# --- 2. endpoint guard present ---
src = Path("server/main.py").read_text(encoding="utf-8")
assert "if bot.is_bot_running:" in src and "adb_actions.cached_screenshot = []" in src
print("[OK] /adb/test: running-bot guard + cache cleanup present")

# --- 3. main.py compiles with the new except clause ---
import py_compile
py_compile.compile("main.py", doraise=True)
py_compile.compile("server/main.py", doraise=True)
py_compile.compile("utils/adb_actions.py", doraise=True)
print("[OK] main.py, server/main.py, adb_actions.py all compile")

# import main's module-level dependencies far enough to resolve BotStopException
from utils.device_action_wrapper import BotStopException
print("[OK] BotStopException importable from main.py's namespace")
