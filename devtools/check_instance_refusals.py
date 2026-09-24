"""What an instance refuses to start on.

Phase 5e. Two failures that were warnings, and warnings are what nobody reads once there
are several logs.

**Two bots on one emulator.** They do not split the work -- they interleave taps on one
screen, and each reads the other's navigation as a screen it did not ask for. The claim is
held by an open file handle rather than a recorded pid, so the operating system releases
it however the process ends; a crash gives the device back instead of stranding it. That
also sidesteps a Windows trap: `os.kill(pid, 0)` does not probe a process there, it calls
TerminateProcess and kills it, so the obvious liveness check would be the worst possible
bug in a guard whose whole job is to protect a running bot.

**A device that is not 800x1080.** Every coordinate and OCR region in the repo is authored
against it. On another size the templates still match something, weakly and in the wrong
place, so the failure is an hour of clicking slightly wrong things rather than a stop.
Checked once on connecting; the per-frame path still only warns, because one odd frame is
a glitch and ending a career over it would be worse than the misalignment.

**And a refusal has to tell the truth about it.** `is_bot_running` was cleared only on the
path that actually ran, so a failure to start left the flag set: the next hotkey press
stopped a corpse and the one after it started anything. Every refusal added here goes
through that same place.

  py devtools/check_instance_refusals.py
"""

import ast
import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.device_claim as claim_module                          # noqa: E402
import utils.adb_actions as adb_actions                           # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def hold_in_subprocess(stats_dir, device_id, name):
  """A real second process holding the claim, because that is the case that matters."""
  source = textwrap.dedent(f"""
      import sys, time
      sys.path.insert(0, {os.getcwd()!r})
      import core.bot as bot
      bot.use_adb = True
      bot.device_id = {device_id!r}
      bot.instance_name = {name!r}
      from core.device_claim import claim
      print("held" if claim({stats_dir!r}) is None else "refused")
      sys.stdout.flush()
      time.sleep(30)
  """)
  child = subprocess.Popen([sys.executable, "-c", source], stdout=subprocess.PIPE,
                           text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
  for _ in range(200):
    line = child.stdout.readline()
    if line.strip() == "held":
      return child
    if not line:
      break
  child.terminate()
  return None


def claim_cases():
  print("\nOne bot per emulator:")
  saved = (bot.use_adb, bot.device_id, bot.instance_name)
  tmp = tempfile.mkdtemp(prefix="check_refusals_")
  child = None
  try:
    bot.use_adb, bot.instance_name = True, "second"
    bot.device_id = "127.0.0.1:9999"
    child = hold_in_subprocess(tmp, "127.0.0.1:9999", "first")
    check(child is not None, "a first process takes the device")

    holder = claim_module.claim(tmp)
    check(holder is not None, f"a second process on the same device is refused, got {holder!r}")
    check(holder and "first" in holder,
          f"and the refusal names who has it, so it is actionable: {holder!r}")

    # A different emulator is a different claim; the guard must not become a global lock.
    bot.device_id = "127.0.0.1:5565"
    other = claim_module.claim(tmp)
    check(other is None, f"a different device is free to be claimed, got {other!r}")
    check(claim_module.claim(tmp) is None,
          "and claiming again inside one process is a no-op, so stop/start still works")
    claim_module.release(tmp)

    # The property a pid file cannot have: the device comes back on its own.
    bot.device_id = "127.0.0.1:9999"
    child.terminate()
    child.wait(timeout=30)
    child = None
    for _ in range(50):
      if claim_module.claim(tmp) is None:
        break
      time.sleep(0.1)
    check(claim_module._held is not None,
          "when the holder dies the device is claimable again, with nothing to clean up")
    claim_module.release(tmp)
  finally:
    if child is not None:
      child.terminate()
    claim_module.release(tmp)
    bot.use_adb, bot.device_id, bot.instance_name = saved
    shutil.rmtree(tmp, ignore_errors=True)


def resolution_cases():
  print("\nA device the coordinates do not fit:")
  saved = adb_actions.device

  class Fake:
    def __init__(self, shape):
      self.shape = shape

    def screenshot(self):
      import numpy as np
      # Content on the frame, not a flat fill: the connect-time check refuses a frame with
      # nothing rendered on it, and every case in this loop is about the frame's size.
      frame = np.zeros(self.shape, dtype=np.uint8)
      frame[::2, ::2] = 255
      return frame

  try:
    for shape, ok, why in (
        ((1080, 800, 3), True, "portrait 800x1080 is what everything is authored against"),
        ((800, 1080, 3), True, "landscape is allowed -- the screenshot path rotates it"),
        ((1920, 1080, 3), False, "a phone-sized device is refused rather than warned about"),
        ((2160, 1600, 3), False, "and so is an exact multiple, which would misalign just "
                                 "as badly while looking plausible")):
      adb_actions.device = Fake(shape)
      got = adb_actions._check_resolution("test:5555")
      check(got is ok, f"{shape[1]}x{shape[0]}: {why}")

    class Broken:
      def screenshot(self):
        raise RuntimeError("no frame")

    adb_actions.device = Broken()
    check(adb_actions._check_resolution("test:5555") is False,
          "a device that connects but cannot be read is refused too")

    # Driven through init_adb rather than only calling the helper: a check nothing
    # consults is not a check, and returning True from init_adb passed every case above.
    class FakeAdb:
      @staticmethod
      def connect(address):
        return None

      @staticmethod
      def device(address):
        return Fake((1920, 1080, 3))

    saved_adb, saved_use, saved_id = adb_actions.adb, bot.use_adb, bot.device_id
    adb_actions.adb = FakeAdb
    bot.use_adb, bot.device_id = True, "test:5555"
    try:
      check(adb_actions.init_adb() is False,
            "connecting to a wrong-sized device fails, so the run never starts")
    finally:
      adb_actions.adb = saved_adb
      bot.use_adb, bot.device_id = saved_use, saved_id
  finally:
    adb_actions.device = saved


def honest_refusal_cases():
  """Every way out of main() has to leave the flag saying what is true."""
  print("\nA refusal says the bot is not running:")
  tree = ast.parse(io.open("main.py", encoding="utf-8").read())
  main = next((node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name == "main"), None)
  check(main is not None, "main() is where the run starts and stops")
  if main is None:
    return

  def clears(statement):
    return (isinstance(statement, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "is_bot_running"
                    for t in statement.targets)
            and isinstance(statement.value, ast.Constant)
            and statement.value.value is False)

  # Every block that returns early, and every block that ends without running, has to
  # clear it. Walked structurally rather than grepped: the point is that a *new* refusal
  # cannot be added without one, and a count of the string would pass while the new
  # branch had none.
  unguarded = []
  for node in ast.walk(main):
    body = getattr(node, "body", None)
    for block in (body, getattr(node, "orelse", None)):
      if not isinstance(block, list):
        continue
      for index, statement in enumerate(block):
        if isinstance(statement, ast.Return):
          if not any(clears(earlier) for earlier in block[:index]):
            unguarded.append(statement.lineno)
  check(not unguarded,
        f"every early return from main clears is_bot_running first, "
        f"offending line(s): {unguarded}")

  source = io.open("main.py", encoding="utf-8").read()
  check("claim_device()" in source, "the device is claimed before the screen is driven")


def main():
  claim_cases()
  resolution_cases()
  honest_refusal_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("An instance refuses a taken device and a device it cannot fit, and says so.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
