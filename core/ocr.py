"""OCR for the bot: PP-OCR models under onnxruntime, via RapidOCR.

Replaced EasyOCR on 2026-09-05. Measured across 18 fields and 66 skill names swept off a
live list: identical readings, 3.5x the speed, and no torch in the process -- which is
1.2 GB of install and a per-instance model load the multi-instance work would otherwise
have to pay N times. `devtools/bench_ocr.py` is the measurement and BACKLOG.md records
it, including why Tesseract is not the answer at either speed.

Two engines, not one, and that difference matters more than the speed does:

  * **recognition only**, for a crop that already holds a single line. This is where the
    speed is -- 11ms against EasyOCR's 40 -- and it is what nearly every reader here
    wants, because nearly every reader crops one cell.
  * **detection and recognition**, for a region holding more than one line. Recognition
    alone does not merely run slower on those, it returns nonsense: the borrow list's
    two-line block comes back as 'Touching lves dLLuck!]' rather than the title and the
    character name.

Detection is the default so that a caller nobody has thought about is slow rather than
wrong, and `use_recognize=True` opts into the fast path. Roughly: pass it when you
cropped one cell, leave it alone when you cropped a panel.

**`allowlist` is weaker here than it was.** EasyOCR and Tesseract both constrain the
decode; PP-OCR cannot, so the list can only filter the output afterwards. It scored the
same on everything measured, but a reader that leans on the allowlist to force an
ambiguous glyph one way is leaning on something that no longer exists. The tier glyph is
the standing example of what this engine will not give you at all, and
`core.independent_skill.tier_glyph` reads that off the pixels instead.
"""

import re

import numpy as np
from PIL import Image

import core.config as config
from utils.log import debug, warning

# The characters a reader gets when it names none. Kept identical to the EasyOCR default
# so that every caller that relied on it reads the same way it did before.
DEFAULT_ALLOWLIST = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     "0123456789-!.,'#? ")

# `_engines` holds one engine per detection mode, and `_built_with` holds the device each of
# those engines was *really* built with: `True` for DirectML, `False` for the CPU fallback.
# That is a record per mode rather than one flag for the process, because a box can serve
# DirectML to one mode and not the other, so two cached engines can genuinely sit on two
# devices at once -- the fact that matters is the one beside the engine being asked for, not
# a summary of the cache. An entry is reused only while its own record equals the device the
# call wants, and the entry that disagrees is the only one rebuilt: see `_engine`.
# `_last_gpu_config` is a different fact again, the config value the previous call saw, kept
# so that a real change to `OCR_USE_GPU` clears `_gpu_failed` and lets DirectML be tried
# again. `_gpu_failed` latches a DirectML failure for the rest of the process.
_engines = {}
_built_with = {}
_gpu_failed = False
_last_gpu_config = None


def _engine(use_detect):
  """A cached RapidOCR, one per detection mode, rebuilt when that mode's device changes.

  The cache is keyed on what the *config* asks for, never on what came back from it. A
  DirectML build that fails falls back to the CPU, latches `_gpu_failed`, and records the
  CPU engine under this mode, so one bad box pays for one model load and prints one warning
  instead of paying both on every later read. Only an actual change to `OCR_USE_GPU`
  clears the latch and lets DirectML be tried again.

  Reuse is decided from the entry's own record rather than from a flag about the cache, so
  a mode whose engine was really built for the device wanted now is kept, and the mode that
  disagrees is the only one rebuilt. A mixed cache -- a DirectML engine for one mode beside
  a CPU one for the other -- is therefore a state this handles, not one it has to destroy.
  """
  global _engines, _gpu_failed, _last_gpu_config
  raw_wanted = bool(getattr(config, "OCR_USE_GPU", False))
  if raw_wanted != _last_gpu_config:
    _gpu_failed = False
    _last_gpu_config = raw_wanted
  wanted_gpu = raw_wanted and not _gpu_failed
  if use_detect in _engines and _built_with.get(use_detect) == wanted_gpu:
    return _engines[use_detect]

  from rapidocr import RapidOCR
  # DirectML, where the box has it. Measured at 1-3% over the CPU on this hardware, which
  # is noise -- it is wired up because the config key exists and a discrete GPU might do
  # better, not because it earns its keep on an integrated one. A build that fails here is
  # not retried until `OCR_USE_GPU` changes, so a box without a working DirectML provider
  # does not rebuild the models on every read.
  engine_gpu = wanted_gpu
  params = {
      "EngineConfig.onnxruntime.use_dml": engine_gpu,
      "Global.use_det": use_detect,
      "Global.use_cls": False,
      "Global.use_rec": True,
      "Global.log_level": "error",
  }
  try:
    _engines[use_detect] = RapidOCR(params=params)
    _built_with[use_detect] = engine_gpu
    debug(f"OCR engine ready (detect={use_detect}, gpu={engine_gpu}).")
  except Exception as exception:  # noqa: BLE001 - a bad GPU setting must not stop the bot
    if not wanted_gpu:
      raise
    warning(f"Could not start OCR with gpu=True ({exception}); falling back to the CPU.")
    # Latched before the rebuild: the DirectML attempt failed, and the fallback engine is
    # what stays recorded for this mode, so this is the one place that says which device is
    # really in use.
    _gpu_failed = True
    # Only this mode's entry goes back to the CPU; the other mode is left exactly where it
    # is, with its own record beside it. The latch has just turned the wanted device off, so
    # the next call for a mode built on DirectML reads True against a wanted False and
    # rebuilds it, while a mode already on the CPU reads False against False and keeps the
    # engine it has. One flag for the whole process could not say that -- which is why the
    # cache used to be emptied here, at the cost of reloading the mode that was already
    # right.
    engine_gpu = False
    params["EngineConfig.onnxruntime.use_dml"] = engine_gpu
    _engines[use_detect] = RapidOCR(params=params)
    _built_with[use_detect] = engine_gpu
    debug(f"OCR engine ready (detect={use_detect}, gpu={engine_gpu}) on the CPU fallback.")
  return _engines[use_detect]


def get_reader():
  """Load the models up front.

  `parse_skill_rows` calls this before fanning reads across a thread pool, so that the
  first-call model load happens once on one thread instead of racing on several.
  """
  _engine(True)
  return _engine(False)


def _as_array(image):
  return np.array(image) if isinstance(image, Image.Image) else np.asarray(image)


def _filter(text, allowlist):
  return "".join(character for character in text if character in allowlist)


def extract_text(pil_img, use_recognize=False, allowlist=None, threshold=None) -> str:
  """Read `pil_img`, as one line when `use_recognize`, otherwise as a whole region.

  `allowlist` filters the result rather than constraining the decode -- see the module
  docstring. `threshold` drops lines the recogniser is less sure of than that.
  """
  if pil_img is None:
    return ""
  image = _as_array(pil_img)
  if image.size == 0:
    return ""
  if allowlist is None:
    allowlist = DEFAULT_ALLOWLIST

  result = _engine(not use_recognize)(image)
  texts = list(getattr(result, "txts", None) or ())
  if not texts:
    return ""
  scores = list(getattr(result, "scores", None) or ())
  boxes = getattr(result, "boxes", None)

  if threshold is not None and scores:
    keep = [i for i, score in enumerate(scores) if float(score) >= threshold]
    texts = [texts[i] for i in keep]
    boxes = None if boxes is None else [boxes[i] for i in keep]

  if boxes is not None and len(boxes) == len(texts) and len(texts) > 1:
    # More than one line came back, so put them in reading order before joining. The
    # borrow matcher depends on this: it matches a card title against a block whose
    # second line is the character name, and the two arriving the wrong way round reads
    # as a different card.
    texts = [sort_ocr_result(list(zip(boxes, texts)))]

  return _filter(" ".join(part for part in texts if part), allowlist).strip()


def sort_ocr_result(results):
  """Join detected fragments top to bottom, then left to right within each row.

  Carried over unchanged from the EasyOCR reader, and still fed the same shape: an
  iterable of (four-point box, text). RapidOCR's boxes index the same way, so the row
  grouping that the borrow list's two-line block depends on did not have to be rewritten.
  """
  sorted_results = sorted(results, key=lambda x: x[0][0][1])
  if len(sorted_results) == 0:
    return ""
  previous_item = sorted_results[0]

  rows = [[]]
  row_number = 0
  for item in sorted_results:
    if item is previous_item:
      rows[row_number].append(item)
      continue
    tolerance = abs(previous_item[0][0][1] - previous_item[0][2][1]) * 0.6
    if (item[0][0][1] < (previous_item[0][0][1] + tolerance)
        and item[0][0][1] > (previous_item[0][0][1] - tolerance)):
      rows[row_number].append(item)
    else:
      row_number += 1
      rows.append([])
      rows[row_number].append(item)
    previous_item = item

  final_text = ""
  for row in rows:
    sorted_row = sorted(row, key=lambda x: x[0][0][0])
    final_text += " ".join([item[1] for item in sorted_row]) + " "
  return re.sub(r"\s+", " ", final_text).strip()
