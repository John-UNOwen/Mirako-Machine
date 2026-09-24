"""The OCR engine cache: one model load per mode, one warning, and the truth about gpu.

Offline, and entirely so. A fake `rapidocr` is injected into `sys.modules`, so nothing
here starts the bot, opens a device, or loads a real model: the fake records every
`RapidOCR(params=...)` construction and the `use_dml` it was asked for, and raises on
`use_dml=True` unless the case says the box has a working DirectML provider -- which is
what a real build without one raises. `core.config.OCR_USE_GPU` is set directly, the way
`core/ocr.py` reads it.

What it is guarding. The cache used to key itself on what came *back* from a build instead
of what the config *asked* for: one variable, `_engines_gpu`, held both facts. A failed
DirectML attempt fell back to the CPU and wrote `gpu=False` into it, so the next call saw
"config wants True, engines are False", cleared the cache, built both models again, and
printed the warning again -- on every read, forever, and for both detection modes because
clearing the cache drops the mode that had nothing to do with the failure. B1, B2 and B3
are the three faces of that bug and all three fail on the unfixed code. `core/ocr.py` now
keeps the device each engine was *really* built with in `_built_with`, one record per
detection mode beside the engine it describes, apart from `_last_gpu_config` (the config
value the previous call saw), and latches the failure in `_gpu_failed`. B1-B6 read that
per-mode record where they used to read the single `_engines_gpu`, and B6 no longer expects
a failed mode to cost the *other* mode a reload: a mode already on the device the effective
setting wants is now kept, which is the point of recording the device per engine.

B7 is the second face of the same split, and the one a single boolean cannot describe: the
device is a *global* fact while the engines are *per mode*, so a box whose DirectML provider
serves detection but not recognition leaves a genuinely DirectML-built engine cached beside
a CPU one. The fallback then claims, through `_engines_gpu`, that the whole cache is on the
CPU, and the invalidation guard -- which compares that one flag, never the entries -- finds
the cache "already on the CPU" and keeps handing the DirectML engine back. B1-B6 all pass on
that code, because every one of them has DirectML working for both modes or for neither.
B7 fails on it, and the fake is therefore able to say *which* mode DirectML works for.

The name the single flag had is retired, and no writer writes it any more. `_built_with`
replaced `_engines_gpu`, and a rename is only finished when every writer moves with it:
`devtools/bench_ocr.py`'s cache reset was the last one holding the old name, and it now
clears `_built_with` beside `_engines` -- which it needs for its own sake, because a bench
run that reset only `_engines` would leave a build record from the previous setting behind.
S1 reads the syntax tree rather than the text, and scans all three writers, because every
docstring that explains the old design names the old name on purpose (this one just did): a
writer of the retired name is an `Attribute` or a `Name` node, where the same characters
inside a docstring are a string.

Warnings and the "OCR engine ready" lines are counted through a logging handler rather
than by patching `utils.log`, so what is asserted is what the bot would actually print.

  py devtools/check_ocr_fallback.py
"""

import ast
import logging
import os
import re
import sys
import types
from types import SimpleNamespace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import core.config as config                                    # noqa: E402
import core.ocr as ocr                                          # noqa: E402

failures = []
verdicts = []

# The recorder behind the fake module. Rebound by `reset()` for every case.
attempts = []                # every RapidOCR(...) call, raised or not
built = []                   # the calls that produced an engine -- "the model was built"
# `dml_works` is the original switch: one boolean for the whole box, which is all B1-B6
# need and what they still write. `dml_works_for` is the per-mode version -- the set of
# detection modes whose DirectML provider is present -- and when it is not None it answers
# instead. A real box can be either: a provider comes from the GPU, but the *build* that
# asks for it is per mode, so detection succeeding while recognition raises is a state the
# fake has to be able to express. See `_dml_available`.
state = {"dml_works": False, "dml_works_for": None}


def _dml_available(use_detect):
  """Whether this build's mode has a working DirectML provider.

  The per-mode set wins where it is set; otherwise the original boolean answers for every
  mode, which is how B1-B6 keep the semantics they were written against.
  """
  works_for = state["dml_works_for"]
  if works_for is None:
    return bool(state["dml_works"])
  return bool(use_detect) in works_for


class FakeRapidOCR:
  """The `RapidOCR` the fake `rapidocr` module hands out.

  `use_dml=True` raises unless `_dml_available` says this build's mode has a provider, which
  is a box that cannot deliver DirectML to that mode; the failed call is recorded in
  `attempts` but never in `built`, so the two questions -- "how often was DirectML tried" and
  "how often was a model loaded" -- stay apart. A built engine is callable and answers like a
  one-line read, so `extract_text` can be driven down the same path the bot uses, and its
  build record carries the engine itself, so a caller can ask what a *returned* engine was
  really built with.
  """

  def __init__(self, params=None):
    params = dict(params or {})
    record = {
        "use_dml": bool(params.get("EngineConfig.onnxruntime.use_dml", False)),
        "use_detect": bool(params.get("Global.use_det", False)),
        "raised": False,
    }
    attempts.append(record)
    if record["use_dml"] and not _dml_available(record["use_detect"]):
      record["raised"] = True
      raise RuntimeError("DirectML provider is not available in this offline case")
    self.use_dml = record["use_dml"]
    self.use_detect = record["use_detect"]
    self.reads = 0
    record["engine"] = self
    built.append(record)

  def __call__(self, image):
    self.reads += 1
    return SimpleNamespace(txts=["Hello World"], scores=[0.99], boxes=None)


fake_module = types.ModuleType("rapidocr")
fake_module.RapidOCR = FakeRapidOCR
sys.modules["rapidocr"] = fake_module


class Capture(logging.Handler):
  """Every line the bot logs, kept rather than printed, so it can be counted."""

  def __init__(self):
    super().__init__(level=logging.DEBUG)
    self.records = []

  def emit(self, record):
    self.records.append(record)


capture = Capture()

# "OCR engine ready (detect=True, gpu=False)" -- what the module *reports* it built.
REPORT = re.compile(r"OCR engine ready \(detect=(True|False), gpu=(True|False)\)")


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def image():
  """A frame `extract_text` will pass on to the engine: not empty is all it asks."""
  return np.zeros((8, 8), dtype=np.uint8)


def reset(dml_works=False, wanted=False, dml_works_for=None):
  """A fresh process, as far as the module can tell.

  An empty cache, no latch, no remembered config, a fresh recorder, and `OCR_USE_GPU` set
  directly -- the whole of the state `_engine` keys itself on.

  `dml_works` is the single boolean B1-B6 use, as they always have. `dml_works_for` is the
  per-mode set, and takes precedence where it is given: `reset(dml_works_for={True},
  wanted=True)` is a box whose DirectML provider serves detection only.
  """
  global attempts, built
  attempts = []
  built = []
  state["dml_works"] = bool(dml_works)
  state["dml_works_for"] = (None if dml_works_for is None
                            else {bool(mode) for mode in dml_works_for})
  ocr._engines = {}
  ocr._built_with = {}
  ocr._gpu_failed = False
  ocr._last_gpu_config = None
  config.OCR_USE_GPU = wanted
  capture.records.clear()


def warnings():
  return [record for record in capture.records if record.levelno >= logging.WARNING]


def reports():
  """The (detect, gpu) each "OCR engine ready" line claims, in the order it was logged."""
  found = []
  for record in capture.records:
    matched = REPORT.search(record.getMessage())
    if matched:
      found.append((matched.group(1) == "True", matched.group(2) == "True"))
  return found


def dml_tries():
  return [attempt for attempt in attempts if attempt["use_dml"]]


def modes():
  return [engine["use_detect"] for engine in built]


def record_of(engine):
  """The build record behind an engine `_engine` handed back, or None if there is none.

  Reads the recorder, not the module: "what was this returned object really built with" is
  the question, and the answer has to come from the side that did the building.
  """
  for record in built:
    if record.get("engine") is engine:
      return record
  return None


def last_cpu_build(use_detect):
  """The most recently built CPU engine for a mode, or None -- the one a cache hit returns."""
  for record in reversed(built):
    if record["use_detect"] == use_detect and record["use_dml"] is False:
      return record["engine"]
  return None


def describe(engine):
  """An engine as what the recorder knows about it, for a failure message."""
  if engine is None:
    return "None (nothing was built)"
  index = next((i for i, record in enumerate(built) if record.get("engine") is engine), None)
  where = f"build#{index}" if index is not None else "an engine the recorder never saw"
  return f"{where} (detect={engine.use_detect}, dml={engine.use_dml})"


def dml_returns(engine):
  """Whether this returned engine was built with DirectML, per its own build record."""
  record = record_of(engine)
  return None if record is None else record["use_dml"]


def run_case(name, title, body):
  """Run one case and print its verdict, so B1-B6 each report on their own line."""
  print(f"\n{name}  {title}")
  before = len(failures)
  body()
  passed = len(failures) == before
  verdicts.append((name, passed))
  print(f"{name}  {'PASS' if passed else 'FAIL'}")


def b1_wanted_but_unavailable():
  """GPU wanted, no DirectML provider: one model load and one warning, not per call.

  Fails on the unfixed code: it wrote the fallback's gpu=False into the variable the cache
  compared against, so every call rebuilt both engines and warned again.
  """
  reset(dml_works=False, wanted=True)
  for _ in range(5):
    ocr._engine(True)
  check(len(built) == 1,
        f"five detect calls with gpu wanted and unavailable build the model once, "
        f"got {len(built)} build(s)")
  check(len(dml_tries()) == 1,
        f"and DirectML is attempted once, got {len(dml_tries())} attempt(s)")
  check(len(warnings()) == 1,
        f"and the fallback warns once rather than once per call, got {len(warnings())}")
  check(bool(built) and built[0]["use_dml"] is False,
        "and the engine that survives the fallback is the CPU one")
  check(ocr._engine(True) is ocr._engine(True),
        "and repeated calls hand back the same engine, not a rebuild")

  # The caller path, not only the cache: reads reach the same engine.
  texts = [ocr.extract_text(image()) for _ in range(5)]
  check(texts == ["Hello World"] * 5,
        f"and five reads through it still return their text, got {texts}")
  check((len(built), len(warnings())) == (1, 1),
        f"with five more reads adding no build and no warning, got "
        f"{len(built)} build(s) and {len(warnings())} warning(s)")


def b2_get_reader_builds_two():
  """`get_reader()` twice: two engines, one per mode -- not four.

  Fails on the unfixed code: each `get_reader()` cleared the cache mid-call and rebuilt
  both modes, so the second call threw away the first call's engines.
  """
  reset(dml_works=False, wanted=True)
  ocr.get_reader()
  ocr.get_reader()
  check(len(built) == 2,
        f"two get_reader() calls build two engines, one per mode, got {len(built)}")
  check(sorted(modes()) == [False, True],
        f"and they are one detect and one recognize engine, got {modes()}")
  check(modes().count(True) == 1 and modes().count(False) == 1,
        f"each mode built exactly once across both calls, got {modes()}")
  # One attempt, not one per mode: the first mode's failure latches before the second is
  # built, so the recognize engine is a plain CPU build and does not pay a doomed DirectML
  # call of its own. What must not happen is an attempt per *call*, which is the bug.
  check(len(dml_tries()) == 1,
        f"and DirectML is attempted once across both calls, not once per mode or call, "
        f"got {len(dml_tries())}")
  check([engine["use_dml"] for engine in built] == [False, False],
        f"with both cached engines really built for the CPU, got "
        f"{[engine['use_dml'] for engine in built]}")


def b3_reported_gpu_is_the_real_one():
  """The gpu the module reports equals the gpu the engines were really built with.

  Fails on the unfixed code, where the success line printed the gpu the config *wanted*
  (True) next to an engine that had just been built for the CPU, and the fallback printed
  nothing at all.
  """
  reset(dml_works=False, wanted=True)
  for _ in range(5):
    ocr._engine(True)
  ocr._engine(False)

  check(len(reports()) == len(built),
        f"every engine built is reported once, got {len(reports())} line(s) for "
        f"{len(built)} engine(s)")
  mismatched = [(reported, (engine["use_detect"], engine["use_dml"]))
                for reported, engine in zip(reports(), built)
                if reported != (engine["use_detect"], engine["use_dml"])]
  check(not mismatched,
        f"and each report names the device its engine was built with, mismatches: {mismatched}")
  check(not any(gpu for _, gpu in reports()),
        f"so nothing claims gpu=True while the engines are on the CPU, got {reports()}")
  check(ocr._built_with == {True: False, False: False},
        f"and the module records the CPU as the device both engines were built with, got "
        f"{ocr._built_with!r}")
  check(all(engine["use_dml"] is False for engine in built),
        "which is what the fake was really asked for, so report and reality agree")


def b4_wanted_and_available():
  """GPU wanted and working: one build per mode, cached across calls, reported as True."""
  reset(dml_works=True, wanted=True)
  detect = ocr._engine(True)
  recognize = ocr._engine(False)
  for _ in range(4):
    ocr._engine(True)
    ocr._engine(False)
  ocr.get_reader()
  for _ in range(5):
    ocr.extract_text(image())
  check(len(built) == 2,
        f"a working GPU costs one build per mode however often it is used, got {len(built)}")
  check(sorted(modes()) == [False, True], f"one detect and one recognize engine, got {modes()}")
  check(all(engine["use_dml"] for engine in built),
        f"both built on DirectML, got {[engine['use_dml'] for engine in built]}")
  check(ocr._engine(True) is detect and ocr._engine(False) is recognize,
        "and later calls hit the cache rather than rebuilding -- no churn")
  check(ocr._built_with == {True: True, False: True},
        f"and the module records DirectML as the device both engines were built with, got "
        f"{ocr._built_with!r}")
  check(ocr._gpu_failed is False, "with no failure latched")
  check(not warnings(), f"and nothing warns, got {[r.getMessage() for r in warnings()]}")
  check(reports() and all(gpu for _, gpu in reports()),
        f"and every report says gpu=True, got {reports()}")


def b5_setting_off():
  """`OCR_USE_GPU` False: one build per mode, cached, and behavior unchanged.

  `dml_works` is True here on purpose -- even where DirectML would work, a setting of
  False must never be asked for it, and nothing downstream may change.
  """
  reset(dml_works=True, wanted=False)
  detect = ocr._engine(True)
  recognize = ocr._engine(False)
  for _ in range(4):
    ocr._engine(True)
    ocr._engine(False)
  check(len(built) == 2,
        f"one build per mode, got {len(built)} for {modes()}")
  check(not dml_tries(),
        f"and DirectML is never asked for, even where it works, got {len(dml_tries())}")
  check(ocr._built_with == {True: False, False: False},
        f"and the module records the CPU as the device both engines were built with, got "
        f"{ocr._built_with!r}")
  check(ocr._engine(True) is detect and ocr._engine(False) is recognize,
        "and the engines are cached, not rebuilt")
  check(not warnings(), "with nothing to warn about")
  check(ocr.extract_text(image()) == "Hello World",
        "behavior is unchanged: a detection read still returns its text")
  check(ocr.extract_text(image(), use_recognize=True) == "Hello World",
        "and so does a recognition read, off the other cached engine")
  check(len(built) == 2,
        f"with both reads off the cache, got {len(built)} build(s)")


def b6_setting_transition():
  """Off and back on again: exactly one new DirectML attempt, one fallback, then cache hits.

  The latch is what makes a transition meaningful: the setting changing is the only thing
  that lets DirectML be tried again, and it must be tried once, not once per call.
  """
  reset(dml_works=False, wanted=True)
  ocr._engine(True)
  ocr._engine(False)
  primed_built, primed_tries = len(built), len(dml_tries())
  check(primed_tries == 1,
        f"priming both modes with a broken GPU attempts DirectML once, got {primed_tries}")
  check(primed_built == 2, f"and leaves both modes on the CPU, got {primed_built} build(s)")

  for _ in range(3):
    ocr._engine(True)
    ocr._engine(False)
  check((len(built), len(dml_tries())) == (primed_built, primed_tries),
        f"steady state rebuilds nothing and re-attempts nothing, got "
        f"{(len(built), len(dml_tries()))} against {(primed_built, primed_tries)}")

  config.OCR_USE_GPU = False
  ocr._engine(True)
  ocr._engine(False)
  check((len(built), len(dml_tries())) == (primed_built, primed_tries),
        f"turning the setting off rebuilds nothing -- they are already CPU engines, got "
        f"{(len(built), len(dml_tries()))}")

  config.OCR_USE_GPU = True
  ocr._engine(True)
  check(len(dml_tries()) == primed_tries + 1,
        f"turning it back on attempts DirectML exactly once more, got "
        f"{len(dml_tries()) - primed_tries} new attempt(s)")
  check(len(built) == primed_built + 1,
        f"and pays exactly one fallback build for it, got {len(built) - primed_built}")
  check(built[-1]["use_dml"] is False,
        "which is a CPU engine, the same fallback as before")
  check(reports() and reports()[-1][1] is False,
        f"reported as gpu=False, got {reports()[-1] if reports() else None}")

  settled_built, settled_tries = len(built), len(dml_tries())
  for _ in range(3):
    ocr._engine(True)
  ocr.get_reader()
  check(len(dml_tries()) == settled_tries,
        f"and later calls attempt DirectML no further, got {len(dml_tries()) - settled_tries}")
  # The recognize engine rebuilt by the transition above is already a CPU engine and the
  # effective setting is off, so the device recorded beside it is the one this call wants.
  # A mode is reused on that record alone, so get_reader() reloads nothing here: churn is
  # what the record is for, and an engine that is already right has no reason to be paid
  # for twice. (The single-flag code answered this by emptying the whole cache on every
  # fallback, which is the reload this case used to assert.)
  check(len(built) == settled_built,
        f"with the recognize engine kept rather than reloaded -- its own record already "
        f"names the device the effective setting wants -- got {len(built) - settled_built} "
        f"new build(s)")
  check(ocr._built_with == {True: False, False: False},
        f"and both modes recorded on the CPU, got {ocr._built_with!r}")


def b7_one_mode_served_and_the_other_not():
  """DirectML serves detection but not recognition: the DirectML engine must not survive.

  The engines are cached per mode and the device each was really built with is recorded
  beside it, so this cache can hold a DirectML detect engine and a CPU recognize engine at
  once -- which is what this box has the moment the fallback happens. The DirectML detect
  engine must still be gone by the next detect call: the latch has turned the wanted device
  off, so True is no longer the device that call wants and the entry is rebuilt.

  Fails on the code where one `_engines_gpu` stood for the whole cache: it was flipped to
  False beside the DirectML detect engine, which stayed in `_engines`; the next call compared
  False against a wanted False, found the cache "already on the CPU", and handed the DirectML
  engine back -- and kept doing it after `OCR_USE_GPU` went off, because a False wanted value
  compares equal to it too.
  """
  reset(dml_works_for={True}, wanted=True)
  detect_dml = ocr._engine(True)
  check(dml_returns(detect_dml) is True,
        f"with DirectML available for detection, the detect build really asks for it, got "
        f"{dml_returns(detect_dml)!r} for {describe(detect_dml)}")

  ocr._engine(False)
  tries_after_fallback = len(dml_tries())
  check(tries_after_fallback == 2,
        f"and recognition's DirectML build raises and falls back, so two DirectML attempts "
        f"have been made by now, got {tries_after_fallback}")

  detect_again = ocr._engine(True)
  check(dml_returns(detect_again) is False,
        f"the next detect call returns an engine whose build record says use_dml=False, got "
        f"{dml_returns(detect_again)!r} for {describe(detect_again)}")

  detect_builds = [record for record in built if record["use_detect"] is True]
  check(len(detect_builds) == 2,
        f"because the DirectML detect engine is replaced rather than reused: detect is built "
        f"a second time, got {len(detect_builds)} detect build(s) out of {modes()}")
  check([record["use_dml"] for record in detect_builds] == [True, False],
        f"the first on DirectML and the second on the CPU, got "
        f"{[record['use_dml'] for record in detect_builds]}")
  check(detect_again is not detect_dml,
        "and the engine in hand is a different object from the DirectML one")
  check(len(dml_tries()) == tries_after_fallback,
        f"with the replacement costing no further DirectML attempt, got "
        f"{len(dml_tries()) - tries_after_fallback} new attempt(s)")

  # Off afterwards. `dml_works_for={True}` still says this box could build DirectML, so
  # anything that reaches for it again has to be the stale cache and not the fake.
  mark = len(built)
  config.OCR_USE_GPU = False
  after_off_detect = ocr._engine(True)
  after_off_recognize = ocr._engine(False)
  returned = [after_off_detect, after_off_recognize]

  segment = built[mark:]
  check(all(record["use_dml"] is False for record in segment),
        f"with the setting off nothing in that stretch is built for DirectML, got "
        f"{[record['use_dml'] for record in segment]}")
  check(all(dml_returns(engine) is False for engine in returned),
        f"and no engine returned after it went off was built for DirectML, got "
        f"{[(describe(engine), dml_returns(engine)) for engine in returned]}")
  check(after_off_detect is last_cpu_build(True),
        f"so _engine(True) returns the most recent CPU detect build, got "
        f"{describe(after_off_detect)} against {describe(last_cpu_build(True))}")
  check(after_off_recognize is last_cpu_build(False),
        f"and _engine(False) the most recent CPU recognize build, got "
        f"{describe(after_off_recognize)} against {describe(last_cpu_build(False))}")


# The name the cache's device record had before it was per mode. Retired: a module may name
# it in prose, and this file does, but nothing this change owns may use it as code.
RETIRED_NAME = "_engines_gpu"
# The files that hold the cache's state, and the whole of what S1 scans: the module itself,
# this harness, and the bench whose reset is the third writer of that state. The repo at
# large is still out of scope -- a tree-wide search would make this suite's verdict depend on
# files with nothing to do with the OCR cache -- but every writer of `_engines` and
# `_built_with` is named here, so the retired name has nowhere left to hide.
OWNED = ("core/ocr.py", "devtools/check_ocr_fallback.py", "devtools/bench_ocr.py")


def retired_uses(tree):
  """The line numbers where this parsed source uses the retired name as code.

  `ocr._engines_gpu = None` is an `Attribute` and a bare `_engines_gpu = None` is a `Name`;
  the same characters inside a docstring or a comment are part of a string constant and
  appear nowhere here. That difference is the point -- prose about the old design is
  allowed to say the old name, and a search for the text cannot tell the two apart.
  """
  lines = []
  for node in ast.walk(tree):
    if isinstance(node, ast.Attribute) and node.attr == RETIRED_NAME:
      lines.append(node.lineno)
    elif isinstance(node, ast.Name) and node.id == RETIRED_NAME:
      lines.append(node.lineno)
  return sorted(lines)


def retired_name_sites(root, paths):
  """(sites, files parsed): where the named files use the retired name as code."""
  sites = []
  parsed = 0
  for relative in paths:
    path = os.path.join(root, relative)
    try:
      with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    except (OSError, SyntaxError, UnicodeDecodeError):
      continue              # unreadable Python is a different suite's business, not this one
    parsed += 1
    sites.extend(f"{relative}:{line}" for line in retired_uses(tree))
  return sites, parsed


def s1_the_retired_name_is_gone():
  """None of the three writers of the cache still uses the retired process-wide name.

  Scoped to the writers rather than the repo: a tree-wide search would make this suite's
  verdict depend on files that have nothing to do with the OCR cache. The bench's reset is
  one of the three -- it assigns `_engines` and `_built_with` -- and it carried the old name
  longer than the fix did; it is scanned here so the rename cannot come undone on the one
  line that was, for a while, deliberately left behind.

  `core/ocr.py` is the module the flag was retired from, this harness reads that module's
  cache, and the bench rebuilds it; a writer of the retired name in any of them is a bug.
  """
  # The scanner itself, on both halves of the distinction it exists to draw.
  check(retired_uses(ast.parse("ocr._engines_gpu = None\n")) == [1]
        and retired_uses(ast.parse("_engines_gpu = None\n")) == [1],
        "the scanner sees a retired name written as an attribute and as a plain name")
  check(retired_uses(ast.parse('"""ocr._engines_gpu was the old flag."""\n')) == [],
        "and does not mistake a docstring naming it for code that uses it")

  sites, parsed = retired_name_sites(ROOT, OWNED)
  check(parsed == len(OWNED),
        f"the scan really read the files this change owns rather than walking past them, got "
        f"{parsed} of {len(OWNED)} parsed")
  check(not sites,
        f"so no writer of the cache uses the retired `{RETIRED_NAME}` as code -- prose may "
        f"name it, a writer may not; got {sites}")


def main():
  root = logging.getLogger()
  previous_level = root.level
  root.setLevel(logging.DEBUG)
  root.addHandler(capture)
  try:
    print("Offline: a fake rapidocr is injected into sys.modules; no bot, no device, "
          "no real model.")
    check(sys.modules.get("rapidocr") is fake_module,
          "the fake rapidocr is what an engine build will import")
    check(getattr(fake_module, "RapidOCR", None) is FakeRapidOCR,
          "and it hands out the recording RapidOCR")

    run_case("B1", "GPU wanted, DirectML unavailable: one build, one warning", b1_wanted_but_unavailable)
    run_case("B2", "get_reader() twice: two engines, one per mode", b2_get_reader_builds_two)
    run_case("B3", "the reported gpu is the one the engines were built with",
             b3_reported_gpu_is_the_real_one)
    run_case("B4", "GPU wanted and available: one build per mode, cached, reported True",
             b4_wanted_and_available)
    run_case("B5", "OCR_USE_GPU off: one build per mode, cached, behavior unchanged",
             b5_setting_off)
    run_case("B6", "off and on again: exactly one new DirectML attempt, then cache hits",
             b6_setting_transition)
    run_case("B7", "DirectML serves detect but not recognize: the DirectML engine is dropped",
             b7_one_mode_served_and_the_other_not)
    run_case("S1", "the cache's retired name is gone from every writer of it",
             s1_the_retired_name_is_gone)

    # The real package is installed in this venv, so a case that reached it would load
    # real models and talk to a real device. Nothing may have replaced or bypassed the fake.
    check(sys.modules.get("rapidocr") is fake_module,
          "and after every case the fake is still the only rapidocr in sys.modules -- "
          "no real model was loaded")
    check(all(isinstance(engine, dict) for engine in built),
          "with every construction accounted for by the recorder, not by a real engine")
  finally:
    root.removeHandler(capture)
    root.setLevel(previous_level)
    config.OCR_USE_GPU = False

  print()
  for name, passed in verdicts:
    print(f"  {name}: {'PASS' if passed else 'FAIL'}")
  if failures:
    print(f"\n{len(failures)} failure(s).")
    return 1
  print("\nOne model load per mode, one warning, and the reported gpu is the real one.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
