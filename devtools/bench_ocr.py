"""Time and score an OCR engine against fields whose true values are known.

Speed alone decides nothing here. Every number this bot reads is either used or refused
on the strength of a sanity check, so an engine that is three times quicker and wrong
twice as often is not three times better -- a misread stat is dropped and the career is
recorded incomplete, and a misread skill name is a skill never bought. This measures both
against the same crops.

  py devtools/bench_ocr.py            # every engine that is installed
  py devtools/bench_ocr.py --repeat 5
  py devtools/bench_ocr.py --engines easyocr,rapidocr

The expected values below were read off the captures by eye on 2026-09-02, field by
field, against the same reference images the screen suite uses. They are ground truth,
not another engine's output, which is the only thing that makes an accuracy column mean
anything.

**The EasyOCR column is not the number that justified replacing it.** Every engine here
is asked exactly what the bot's readers ask, and since the swap those readers ask for a
single line, because that is PP-OCR's cheap path. EasyOCR was never called that way for
these fields -- it ran detection on all but the aptitude cells -- and it scores worse in
the mode it never ran in. The like-for-like comparison, both engines under the readers as
they stood before the swap, is the table in BACKLOG.md, and that is the decision record.

Engines are reported only when they import, so an absent one is a line saying so
rather than a crash. `tesseract` shells out to tesseract.exe through pytesseract;
`tesseract-capi` is the same engine reached in process through libtesseract-5.dll;
`rapidocr` and `rapidocr-dml` are PP-OCR under onnxruntime, on the CPU and on the
GPU through DirectML.
"""

import argparse
import os
import shutil
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                              # noqa: E402
import core.config as config                                        # noqa: E402

ADB_REFS = "references/independent_training_adb"

# Verified by eye, 2026-09-02, against these exact files.
TRAINING_LOG_TRUTH = {
  "speed": "1123", "stamina": "741", "power": "933", "guts": "749", "wit": "760",
  "skill_points": "3877", "fans": "695879",
}
RECORD_TRUTH = ("30", "28")          # "Races: 30   Wins: 28"
APTITUDE_TRUTH = {
  "turf": "A", "dirt": "A", "sprint": "F", "mile": "A", "medium": "A",
  "long": "G", "front": "G", "pace": "A", "late": "A", "end": "B",
}

DIGITS = "0123456789"


def _readers(constants):
  """(label, call, expected) using the bot's own readers, not raw engine calls.

  This is the distinction that decides whether the number means anything. Every field
  here goes through a wrapper that does real work on top of the engine: the stat cells
  repaint their furniture and re-read when the value exceeds what any stat reaches, the
  aptitude cells upscale four times and ask for a single line, and the record line is a
  regex over free text. Timing raw crops instead measures an engine nobody runs, and
  scoring them understates every wrapper that exists precisely to fix a known misread.
  """
  import scenarios.independent_training as it
  window_x = constants.GAME_WINDOW_BBOX[0]
  log = cv2.cvtColor(cv2.imread(f"{ADB_REFS}/training_log.png"), cv2.COLOR_BGR2RGB)
  career = cv2.cvtColor(cv2.imread(f"{ADB_REFS}/complete_career.png"), cv2.COLOR_BGR2RGB)
  crop = lambda img, b: img[b[1]:b[3], b[0] - window_x:b[2] - window_x]
  cases = []

  for name, bbox in constants.INDEPENDENT_LOG_STAT_BBOXES.items():
    cell = crop(log, bbox)
    cases.append((f"stat/{name}",
                  (lambda c=cell, n=name: it.stat_from_cell(c, n)),
                  TRAINING_LOG_TRUTH[name]))
  for name, bbox in (("skill_points", constants.INDEPENDENT_LOG_SKILL_PTS_BBOX),
                     ("fans", constants.INDEPENDENT_LOG_FANS_BBOX)):
    cell = crop(log, bbox)
    allow = DIGITS + ("," if name == "fans" else "")
    cases.append((f"log/{name}",
                  (lambda c=cell, a=allow: it._digits_in(c, allowlist=a)),
                  TRAINING_LOG_TRUTH[name]))

  record_cell = crop(log, constants.INDEPENDENT_LOG_RECORD_BBOX)
  def read_record(cell=record_cell):
    import re
    text = it.extract_text(it.enhance_for_ocr_text(cell), use_recognize=True,
                           allowlist="RacesWin:0123456789 ") or ""
    return "".join(re.findall(r"\d+", text)[:2])
  cases.append(("log/record", read_record, "".join(RECORD_TRUTH)))

  for name, bbox in constants.INDEPENDENT_APTITUDE_BBOXES.items():
    cell = crop(career, bbox)
    cases.append((f"apt/{name}", (lambda c=cell: it.grade_from_cell(c)),
                  APTITUDE_TRUTH[name]))
  return cases


def _normalise(value):
  """Compare on the characters that carry meaning, not on spacing or separators.

  The readers already strip these before the bot uses them -- digits come out through a
  regex, an aptitude is one letter -- so counting a comma or a stray space as a miss
  would measure something no caller ever sees. A None reading normalises to "", which
  never equals an expected value, so a refusal counts as a miss rather than passing.
  """
  return "".join(c for c in str(value if value is not None else "").upper() if c.isalnum())


from core.ocr import sort_ocr_result                                # noqa: E402


def _install(func):
  """Point the bot's readers at `func`. They bound extract_text at import time."""
  import scenarios.independent_training as it
  import core.independent_skill as sk
  it.extract_text = func
  sk.extract_text = func


def easyocr_engine():
  """The engine this bot used until 2026-09-05, reached directly rather than through
  core.ocr, which is RapidOCR now. The wrapper below is the one core.ocr used to hold,
  kept here so the comparison that justified the switch can still be re-run.
  """
  import easyocr
  import numpy as np

  reader = easyocr.Reader(["en"], gpu=False)
  default = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-!.,'#? ")

  def extract_text(pil_img, use_recognize=False, allowlist=None, threshold=None):
    image = np.array(pil_img)
    allowed = default if allowlist is None else allowlist
    call = reader.recognize if use_recognize else reader.readtext
    result = (call(image, allowlist=allowed, text_threshold=threshold)
              if threshold is not None else call(image, allowlist=allowed))
    return sort_ocr_result(result)
  return lambda: _install(extract_text)


def tesseract_engine():
  """pytesseract, if both the wrapper and the binary are present."""
  import pytesseract
  # The Windows installer does not put tesseract.exe on PATH for an already-open shell,
  # so look where it actually lands before giving up on it.
  if not shutil.which("tesseract"):
    for candidate in (os.path.join("C:/Program Files/Tesseract-OCR", "tesseract.exe"),
                      os.path.join("C:/Program Files (x86)/Tesseract-OCR",
                                   "tesseract.exe")):
      if os.path.exists(candidate):
        pytesseract.pytesseract.tesseract_cmd = candidate
        break
  pytesseract.get_tesseract_version()   # raises when the binary is missing
  import numpy as np
  from PIL import Image

  def extract_text(pil_img, use_recognize=False, allowlist=None, threshold=None):
    image = pil_img if isinstance(pil_img, Image.Image) else Image.fromarray(np.array(pil_img))
    # --psm 7 is "one text line", the nearest thing to EasyOCR's recognize(); --psm 10
    # is "one character", which is what the aptitude cells actually are.
    psm = 10 if (allowlist == "SABCDEFG") else 7
    cfg = f"--psm {psm}"
    if allowlist:
      cfg += f" -c tessedit_char_whitelist={allowlist}"
    return pytesseract.image_to_string(image, config=cfg).strip()
  return lambda: _install(extract_text)


def tesseract_capi_engine():
  """The same Tesseract, in process -- no tesseract.exe spawned per call.

  There is no `tesserocr` wheel for this interpreter and building one needs a toolchain,
  which is where the earlier measurement stopped. It turns out not to be needed: the
  Windows installer ships `libtesseract-5.dll` beside the exe, and its C API is a dozen
  ctypes declarations. That deletes the subprocess, which was 94% of a pytesseract call.
  """
  import ctypes
  import numpy as np

  root = next((path for path in ("C:/Program Files/Tesseract-OCR",
                                 "C:/Program Files (x86)/Tesseract-OCR")
               if os.path.exists(os.path.join(path, "libtesseract-5.dll"))), None)
  if root is None:
    raise FileNotFoundError("libtesseract-5.dll not found beside tesseract.exe")
  os.add_dll_directory(root)
  lib = ctypes.CDLL(os.path.join(root, "libtesseract-5.dll"))
  lib.TessBaseAPICreate.restype = ctypes.c_void_p
  lib.TessBaseAPIInit3.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
  lib.TessBaseAPIInit3.restype = ctypes.c_int
  lib.TessBaseAPISetImage.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4
  lib.TessBaseAPISetPageSegMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
  lib.TessBaseAPISetVariable.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
  lib.TessBaseAPISetVariable.restype = ctypes.c_int
  lib.TessBaseAPIGetUTF8Text.argtypes = [ctypes.c_void_p]
  lib.TessBaseAPIGetUTF8Text.restype = ctypes.c_void_p
  lib.TessDeleteText.argtypes = [ctypes.c_void_p]

  api = lib.TessBaseAPICreate()
  if lib.TessBaseAPIInit3(api, os.path.join(root, "tessdata").encode(), b"eng") != 0:
    raise RuntimeError("TessBaseAPIInit3 failed")
  # A degenerate crop otherwise makes the engine dump a page of row statistics to stderr.
  lib.TessBaseAPISetVariable(api, b"debug_file", b"NUL")

  def extract_text(pil_img, use_recognize=False, allowlist=None, threshold=None):
    # SetImage does not copy, so this buffer has to outlive the GetUTF8Text below it.
    image = np.ascontiguousarray(np.array(pil_img))
    height, width = image.shape[:2]
    depth = 1 if image.ndim == 2 else image.shape[2]
    # psm 10 is "one character", which is what an aptitude cell is; 7 is "one line".
    lib.TessBaseAPISetPageSegMode(api, 10 if allowlist == "SABCDEFG" else 7)
    lib.TessBaseAPISetVariable(api, b"tessedit_char_whitelist", (allowlist or "").encode())
    lib.TessBaseAPISetImage(api, image.ctypes.data, width, height, depth, width * depth)
    pointer = lib.TessBaseAPIGetUTF8Text(api)
    if not pointer:
      return ""
    try:
      return ctypes.string_at(pointer).decode("utf-8", "replace").strip()
    finally:
      lib.TessDeleteText(pointer)
  return lambda: _install(extract_text)


def rapidocr_engine(use_dml):
  """What the bot actually runs now -- core.ocr itself, not a copy of it.

  Going through the shipped reader is the point: it is what decides per call whether to
  run detection, and measuring a hand-rolled recognition-only shim instead would report
  a number no caller ever sees.
  """
  import core.ocr as ocr
  config.OCR_USE_GPU = use_dml
  ocr._engines, ocr._built_with = {}, {}            # drop every engine and its device
  ocr.get_reader()                                  # pay the model load before timing
  return lambda: _install(ocr.extract_text)


ENGINES = (("easyocr", easyocr_engine),
           ("tesseract", tesseract_engine),
           ("tesseract-capi", tesseract_capi_engine),
           ("rapidocr", lambda: rapidocr_engine(False)),
           ("rapidocr-dml", lambda: rapidocr_engine(True)))


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--repeat", type=int, default=3)
  parser.add_argument("--engines", default="",
                      help="comma-separated subset, e.g. easyocr,tesseract-capi")
  args = parser.parse_args()
  wanted = [name for name in args.engines.split(",") if name]

  config.reload_config()
  bot.use_adb = True
  import utils.constants as constants
  constants.adjust_constants_x_coords(offset=-155)

  cases = _readers(constants)
  print(f"{len(cases)} fields with verified expected values, "
        f"{args.repeat} timed repeats each\n")

  available = []
  for name, factory in ENGINES:
    if wanted and name not in wanted:
      continue
    try:
      available.append((name, factory()))
    except Exception as exception:  # noqa: BLE001 - absence is the expected case
      print(f"  {name}: unavailable -- {type(exception).__name__}: {exception}")
  if not available:
    raise SystemExit("No OCR engine available.")
  print()

  results = {}
  for name, install in available:
    install()
    hits, misses, total_seconds = 0, [], 0.0
    for label, call, expected in cases:
      try:
        call()                                    # warm
        start = time.time()
        for _ in range(args.repeat):
          value = call()
        total_seconds += (time.time() - start) / args.repeat
      except Exception as exception:  # noqa: BLE001 - a crash is a miss, not a stop
        misses.append((label, expected, f"<{type(exception).__name__}>")); continue
      if _normalise(value) == _normalise(expected):
        hits += 1
      else:
        misses.append((label, expected, value))
    results[name] = (hits, len(cases), total_seconds / len(cases), misses)

  print(f"{'engine':12} {'correct':>12} {'mean ms/field':>15} {'total ms/screen':>17}")
  for name, (hits, total, mean, _) in results.items():
    print(f"{name:12} {f'{hits}/{total}':>12} {mean*1000:>15.1f} {mean*1000*total:>17.0f}")

  for name, (_, _, _, misses) in results.items():
    if misses:
      print(f"\n{name} missed {len(misses)}:")
      for label, expected, got in misses:
        print(f"   {label:20} expected {expected!r:12} got {got!r}")

  if len(results) > 1 and "easyocr" in results:
    base_hits, base_total, base_mean, _ = results["easyocr"]
    print()
    for name, (hits, total, mean, _) in results.items():
      if name == "easyocr":
        continue
      print(f"{name} is {base_mean/mean:.2f}x the speed of easyocr, "
            f"at {hits}/{total} correct against {base_hits}/{base_total}.")


if __name__ == "__main__":
  main()
