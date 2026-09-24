"""The desktop paths of focus_umamusume: which window is claimed, in which frame.

Two defects from the review of 8f5ae63 (2026-09-23), both in main.py:

  * The window-name fallback -- an emulator without ADB, BlueStacks by default -- found
    its window, went full screen, and returned without setting bot.windows_window. Every
    desktop capture reads that window, so the first one raised "Couldn't find the
    windows_window somehow": the path could never have run a career.
  * The Steam path never rebased the constants. They are authored in its frame, so a
    first run was fine, but one process runs the bot many times, and a Steam run after
    an ADB run (or a window-fallback run) kept the other frame's coordinates.

Driven against fake windows, since the real ones need a desktop with the game open.

  py devtools/check_focus_window.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = sys.argv[:1]

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


class FakeWindow:
  def __init__(self, title, width=1920, height=1080):
    self.title, self.width, self.height = title, width, height
    self.left = self.top = 0
    self.isMinimized = False

  def restore(self):
    self.isMinimized = False

  def minimize(self):
    self.isMinimized = True


class FakeWindows:
  def __init__(self, *windows):
    self.windows = windows

  def getWindowsWithTitle(self, title):
    return [w for w in self.windows if title.casefold() in w.title.casefold()]


import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402
import utils.constants as constants                               # noqa: E402
import main as main_module                                        # noqa: E402

# focus_umamusume imports pyautogui when it runs, so a stand-in put in its place after
# everything else has the real one keeps these cases off the real screen and keyboard.
sys.modules["pyautogui"] = types.SimpleNamespace(
  press=lambda *a, **k: None, click=lambda *a, **k: None,
  locateCenterOnScreen=lambda *a, **k: None)

main_module.sleep = lambda *a, **k: None
main_module.time = types.SimpleNamespace(sleep=lambda *a, **k: None)

STEAM_FRAME = (155, 0, 955, 1080)
WINDOW_FRAME = (560, 0, 1360, 1080)


def focus(windows, window_name="Bluestacks Umamusume"):
  main_module.gw = windows
  config.WINDOW_NAME = window_name
  bot.use_adb = False
  bot.windows_window = None
  return main_module.focus_umamusume()


def fallback_cases():
  print("The window-name fallback claims its window")
  constants.adjust_constants_x_coords(offset=0)
  emulator = FakeWindow("Bluestacks Umamusume")
  answer = focus(FakeWindows(emulator))
  check(answer is True, "an emulator window found by name is a success")
  check(bot.windows_window is emulator,
        "and it is the window every capture reads -- it was left None, so the first "
        "capture raised")
  check(constants.GAME_WINDOW_BBOX == WINDOW_FRAME,
        f"in the window frame: {constants.GAME_WINDOW_BBOX}")

  answer = focus(FakeWindows(FakeWindow("bluestacks umamusume")))
  check(answer is True, "matched without regard to case, as the Steam title is")


def steam_cases():
  print("\nThe Steam path puts the constants back in its own frame")
  for first, label in ((-155, "an ADB run"), (405, "a window-fallback run")):
    constants.adjust_constants_x_coords(offset=first)
    steam = FakeWindow("Umamusume")
    answer = focus(FakeWindows(steam))
    check(answer is True and bot.windows_window is steam, f"after {label}, Steam is claimed")
    check(constants.GAME_WINDOW_BBOX == STEAM_FRAME,
          f"and the constants are back in the frame they are authored in: "
          f"{constants.GAME_WINDOW_BBOX}")

  answer = focus(FakeWindows(FakeWindow("Umamusume", 1280, 720)))
  check(answer is False, "a Steam window below 1920x1080 is refused with False, not None")
  constants.adjust_constants_x_coords(offset=0)


def main():
  fallback_cases()
  steam_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("Each desktop path claims the window it found, in the frame it runs in.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
