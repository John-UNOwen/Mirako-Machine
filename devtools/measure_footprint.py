"""What one instance costs in memory, and what two cost together.

Phase 5a of the multi-instance plan. The question is not academic: multi-instance is N
processes, forced by three module-level singletons, so "can this machine run two" is
answered by resident memory and nothing else. The backlog sized this against torch and
EasyOCR; the OCR is RapidOCR on onnxruntime now, so the old estimate is worthless and this
measures rather than guesses.

**The skill survey is the peak.** It is the only thing that fans OCR across a thread pool
(`parse_skill_rows`, four workers), and it loads both engines -- one with detection for the
names, one without for the costs. Everything else the bot does is one recognize at a time.
So the survey is what has to fit, N times over.

Measured offline against a reference capture. The survey is read-only -- it scrolls and
reads, and the purchase pass is what clicks -- so nothing here touches a game, buys a
skill, or needs an emulator running. That also makes it repeatable, which a measurement
taken against a live career is not.

Peak working set comes from the Windows API rather than psutil, which is not a dependency
of this repo and is not worth becoming one for a number this tool can read directly.

  py devtools/measure_footprint.py                 one instance
  py devtools/measure_footprint.py --workers 2     two at once, which is the real question
"""

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CAPTURE = "references/independent_training_adb/learn.png"
REFERENCE_DIR = "references/independent_training_adb"
SURVEY_FRAMES = 11          # what a real survey walked on 2026-09-07, from the log
IDENTIFY_FRAMES = 10        # enough to average out a cold template cache


class _MemoryCounters(ctypes.Structure):
  _fields_ = [("cb", wintypes.DWORD),
              ("PageFaultCount", wintypes.DWORD),
              ("PeakWorkingSetSize", ctypes.c_size_t),
              ("WorkingSetSize", ctypes.c_size_t),
              ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
              ("QuotaPagedPoolUsage", ctypes.c_size_t),
              ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
              ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
              ("PagefileUsage", ctypes.c_size_t),
              ("PeakPagefileUsage", ctypes.c_size_t)]


class _MemoryStatus(ctypes.Structure):
  _fields_ = [("dwLength", wintypes.DWORD),
              ("dwMemoryLoad", wintypes.DWORD),
              ("ullTotalPhys", ctypes.c_ulonglong),
              ("ullAvailPhys", ctypes.c_ulonglong),
              ("ullTotalPageFile", ctypes.c_ulonglong),
              ("ullAvailPageFile", ctypes.c_ulonglong),
              ("ullTotalVirtual", ctypes.c_ulonglong),
              ("ullAvailVirtual", ctypes.c_ulonglong),
              ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


# Declared rather than left to ctypes' defaults. A HANDLE comes back as c_int without
# this, which on a 64-bit process truncates the pseudo-handle GetCurrentProcess returns;
# GetProcessMemoryInfo then fails and, because nothing checks it, every reading is a
# clean-looking zero. That is exactly how the first run of this tool reported 0 MB four
# times over, so the call is now checked as well as declared.
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
_kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def rss_mb():
  """Working set now, and the peak this process has ever reached, in MB."""
  counters = _MemoryCounters()
  counters.cb = ctypes.sizeof(counters)
  if not _psapi.GetProcessMemoryInfo(_kernel32.GetCurrentProcess(),
                                     ctypes.byref(counters), counters.cb):
    raise ctypes.WinError(ctypes.get_last_error())
  return counters.WorkingSetSize / 1048576, counters.PeakWorkingSetSize / 1048576


def system_mb():
  """Total and available physical memory, in MB."""
  status = _MemoryStatus()
  status.dwLength = ctypes.sizeof(status)
  if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
    raise ctypes.WinError(ctypes.get_last_error())
  return status.ullTotalPhys / 1048576, status.ullAvailPhys / 1048576


def measure_one(frames=SURVEY_FRAMES, quiet=False):
  """Import what an instance imports, load the OCR, run a survey. Returns the marks."""
  marks = {}
  marks["bare python"] = rss_mb()[0]

  import core.bot as bot
  import core.config as config
  config.reload_config()
  bot.use_adb = True
  import utils.constants as constants
  constants.adjust_constants_x_coords(offset=-155)
  # The scenario drags in the screens, the templates, cv2 and numpy -- everything a real
  # instance holds resident. The server is imported too because uvicorn runs in the same
  # process as the bot, so leaving it out would understate an instance by whatever FastAPI
  # costs.
  import scenarios.independent_training                            # noqa: F401
  import core.independent_skill as skill
  try:
    import server.main                                             # noqa: F401
  except Exception as exception:                                   # noqa: BLE001
    if not quiet:
      print(f"  (server not imported: {exception})")
  marks["+ bot modules"] = rss_mb()[0]

  import cv2
  import numpy as np
  full = cv2.cvtColor(cv2.imread(CAPTURE), cv2.COLOR_BGR2RGB)
  box = constants.SCROLLING_SKILL_SCREEN_BBOX
  region = np.ascontiguousarray(full[box[1]:box[3], box[0]:box[2]])

  skill.get_reader()                    # both engines, the way the survey loads them
  marks["+ OCR models"] = rss_mb()[0]

  started = time.time()
  rows = 0
  for _ in range(frames):
    rows += len(skill.parse_skill_rows(region))
  marks["survey peak"] = rss_mb()[1]
  marks["_seconds"] = time.time() - started
  marks["_rows"] = rows

  # The survey is a burst of a few seconds once a career. This is the load that never
  # stops: every pass of the loop identifies the screen before it can do anything, and it
  # costs more per frame than the whole survey costs per career. If anything decides how
  # many instances a box can carry it is this, not the peak this tool was sent to find.
  import scenarios.independent_screens as screens
  screens.identify_screen(full)                       # warm the template cache
  started = time.time()
  for _ in range(IDENTIFY_FRAMES):
    screens.identify_screen(full)
  marks["_identify_ms"] = (time.time() - started) / IDENTIFY_FRAMES * 1000
  marks["identify peak"] = rss_mb()[1]
  return marks


def report_one(marks):
  order = ["bare python", "+ bot modules", "+ OCR models", "survey peak",
           "identify peak"]
  previous = 0.0
  for name in order:
    value = marks[name]
    print(f"  {name:16} {value:8.0f} MB   {value - previous:+8.0f}")
    previous = value
  print(f"  {'':16} survey   {marks['_rows']} rows in {marks['_seconds']:.1f}s")
  print(f"  {'':16} identify {marks['_identify_ms']:.0f} ms per frame")


def profile_identify():
  """What identifying a screen costs, against how deep its spec sits in SCREEN_ORDER.

  `identify_screen` walks the order and stops at the first match, so a frame costs the
  sum of every spec tried before the one that answers. That makes it the bot's largest
  CPU cost by a wide margin -- more per frame than the whole skill survey costs per
  career -- and the lever on it is position, not threads: capping cv2's pool changes
  nothing measurable, because the matching is effectively single-threaded.

  Reported as a per-spec rate so the ordering question is visible. A screen the bot visits
  constantly, sitting late in the order, pays for every spec in front of it every time.
  """
  import cv2
  import scenarios.independent_screens as screens

  order = [spec.name for spec in screens.SCREEN_ORDER]
  print()
  print(f"Identifying a screen, by depth in SCREEN_ORDER ({len(order)} specs):")
  rows = []
  for capture in sorted(os.listdir(REFERENCE_DIR)):
    if not capture.endswith(".png"):
      continue
    image = cv2.imread(os.path.join(REFERENCE_DIR, capture))
    if image is None or image.shape[:2] != (1080, 800):
      continue
    frame = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = screens.identify_screen(frame)          # warm, and see what it answers
    if not result.matched:
      continue
    started = time.time()
    for _ in range(3):
      screens.identify_screen(frame)
    milliseconds = (time.time() - started) / 3 * 1000
    depth = order.index(result.screen) + 1 if result.screen in order else len(order)
    rows.append((depth, milliseconds, result.screen))

  for depth, milliseconds, name in sorted(rows)[:12]:
    print(f"  #{depth:<3} {name!s:26} {milliseconds:6.0f} ms   "
          f"{milliseconds / depth:5.1f} ms per spec tried")
  if rows:
    per_spec = sum(m / d for d, m, _ in rows) / len(rows)
    print()
    print(f"  {per_spec:.0f} ms per spec on average, so a frame that matches "
          f"nothing costs about {per_spec * len(order) / 1000:.1f}s")


def watch_available(stop, floor):
  """Lowest available physical memory seen while the workers run."""
  while not stop.is_set():
    floor[0] = min(floor[0], system_mb()[1])
    time.sleep(0.2)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--workers", type=int, default=1,
                      help="how many instances to run at once")
  parser.add_argument("--frames", type=int, default=SURVEY_FRAMES)
  parser.add_argument("--profile", action="store_true",
                      help="what identifying a screen costs, by depth")
  parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
  arguments, _ = parser.parse_known_args()

  if arguments.child:
    print(json.dumps(measure_one(arguments.frames, quiet=True)))
    return 0

  total, available = system_mb()
  print(f"\nMachine: {total:.0f} MB total, {available:.0f} MB available before starting.")

  if arguments.profile:
    import core.bot as bot
    import core.config as config
    config.reload_config()
    bot.use_adb = True
    import utils.constants as constants
    constants.adjust_constants_x_coords(offset=-155)
    profile_identify()
    return 0

  if arguments.workers == 1:
    print("\nOne instance, through a skill survey:")
    report_one(measure_one(arguments.frames))
    print(f"\n  available now: {system_mb()[1]:.0f} MB")
    return 0

  print(f"\n{arguments.workers} instances, started together:")
  stop = threading.Event()
  floor = [available]
  watcher = threading.Thread(target=watch_available, args=(stop, floor), daemon=True)
  watcher.start()
  children = [subprocess.Popen(
      [sys.executable, os.path.abspath(__file__), "--child",
       "--frames", str(arguments.frames)],
      stdout=subprocess.PIPE, text=True,
      env={**os.environ, "PYTHONIOENCODING": "utf-8"})
      for _ in range(arguments.workers)]
  results = []
  for child in children:
    out, _ = child.communicate()
    line = next((row for row in out.splitlines() if row.startswith("{")), None)
    if line:
      results.append(json.loads(line))
  stop.set()
  watcher.join(timeout=2)

  for index, marks in enumerate(results, 1):
    print(f"\n  instance {index}:")
    report_one(marks)
  if results:
    peaks = [m["identify peak"] for m in results]
    identify = [m["_identify_ms"] for m in results]
    print(f"\n  summed peak      {sum(peaks):8.0f} MB")
    print(f"  lowest available {floor[0]:8.0f} MB   (of {total:.0f} MB)")
    print(f"  identify         {min(identify):.0f}-{max(identify):.0f} ms per "
          f"frame, each instance")
  return 0


if __name__ == "__main__":
  sys.exit(main())
