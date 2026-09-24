"""An instance is what it was told it is, not what was free when it started.

Phase 5b. `bot.instance` was assigned from whichever web port the scan reached first, so
nothing declared which emulator a process was driving: start the second one while the
first is down and it *becomes* the first, taking the other's log directory and, once state
is keyed on identity, the other's queue.

Three things are checked, and the third is the one that was actually broken rather than
merely fragile.

**The label**, which is the declared name where there is one and the old positional hotkey
where there is not. The fallback is the compatibility promise: a single instance with no
flags has to behave exactly as it did.

**The directory**, which used to be decided in three separate places by three copies of
`if bot.hotkey == "f1"` (`init_logging`, the debug-image write, and the rotate). Three
copies of a rule is three chances for one of them to be missed when the rule changes, and
the rule has just changed.

**The Discord marker**, which was not positional at all -- it was constant. `_STOP_STYLES`
is a module-level dict of f-strings, evaluated at import, when `bot.instance` is still its
default. Every instance's stop notification claimed to be instance 1, for the life of the
process, whichever emulator it was driving. Nothing caught it because the string looks
right in the source and is only wrong at runtime, which is exactly what a check is for.

  py devtools/check_instance_identity.py
"""

import ast
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import utils.log as log                                           # noqa: E402
import utils.cli as cli                                           # noqa: E402
import utils.webhook as webhook                                   # noqa: E402
from utils.webhook import StopReason                              # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def identity(name="", hotkey="f1"):
  """Set the process identity and read back what everything derives from it."""
  bot.instance_name, bot.hotkey = name, hotkey
  return bot.instance_label(), bot.instance_dir()


def label_cases():
  print("\nWhat an instance calls itself:")
  saved = (bot.instance_name, bot.hotkey)
  try:
    check(identity() == ("f1", ""),
          f"undeclared, the first instance is unchanged: {identity()}")
    check(identity(hotkey="f3") == ("f3", "f3"),
          f"undeclared, the rest keep their positional hotkey: {identity(hotkey='f3')}")
    check(identity("left") == ("left", "left"),
          f"declared, it uses its name: {identity('left')}")
    # The point of the whole phase: the name has to outrank the accident.
    check(identity("left", "f2")[0] == "left" and identity("right", "f1")[0] == "right",
          "and the name wins over the port it happened to get")
    check(identity("left", "f1")[1] == "left",
          "a declared instance gets its own directory even when it started first -- "
          "otherwise the first one to start is still special, which is the bug")
  finally:
    bot.instance_name, bot.hotkey = saved


def directory_cases():
  print("\nWhere its files go, decided in one place:")
  source = io.open("utils/log.py", encoding="utf-8").read()
  tree = ast.parse(source)
  # The three copies of the rule are gone, so nothing in this module reads bot.hotkey to
  # decide a path any more. Asserted structurally because the failure is a stale fourth
  # copy that keeps working for f1 and quietly writes the wrong place for everything else.
  reads = [node for node in ast.walk(tree)
           if isinstance(node, ast.Attribute) and node.attr == "hotkey"
           and isinstance(node.value, ast.Name) and node.value.id == "bot"]
  check(not reads,
        f"utils/log.py no longer decides a path from bot.hotkey ({len(reads)} left)")
  # Every path this module writes to is under the instance's own folder. Counted as "at
  # least one and no other rule", not as an exact number: the images folder used to
  # rebuild the whole path a third time and now hangs off log_dir, which is fewer call
  # sites for the same guarantee. What must never come back is a path assembled from
  # something other than instance_dir() -- the bot.hotkey check above is the other half.
  check(source.count("bot.instance_dir()") >= 2,
        f"paths go through instance_dir(), got {source.count('bot.instance_dir()')}")
  check('os.path.join(log_dir, "images")' in source,
        "and the images folder hangs off log_dir rather than rebuilding the path again")

  saved = (bot.instance_name, bot.hotkey)
  try:
    # os.path.join with "" keeps the top level, which is what the first instance had.
    check(os.path.join("logs", identity()[1]) == os.path.join("logs", ""),
          "the undeclared first instance still owns logs/ itself")
    check(os.path.join("logs", identity("left")[1]) == os.path.join("logs", "left"),
          "a declared one owns logs/<name>/")
  finally:
    bot.instance_name, bot.hotkey = saved


def marker_cases():
  print("\nWhat Discord is told:")
  saved = (bot.instance_name, bot.hotkey)
  try:
    # Read out of the dict the way send_stopped does, so a title that went back to being
    # baked in at import fails here rather than looking fine.
    _, bare = webhook._STOP_STYLES[StopReason.STUCK]
    check("Instance" not in bare,
          f"the stored title carries no instance marker, got {bare!r}")

    bot.instance_name, bot.hotkey = "", "f1"
    first = webhook._titled(bare)
    bot.instance_name = "right"
    second = webhook._titled(bare)
    check(first != second,
          "the marker changes when the identity does -- it was frozen at import before, "
          f"so every instance said the same thing: {first!r}")
    check(second.endswith("(Instance right)"),
          f"and it says the declared name, got {second!r}")

    # Driven through send_stopped rather than assembled here. Reading the dict and
    # calling _titled by hand tests both halves and not the join between them: dropping
    # _titled from the call site sends an unmarked title and passed every check above.
    posted = []
    saved_post = webhook._post
    webhook._post = posted.append
    try:
      bot.instance_name = "right"
      webhook.send_stopped(StopReason.STUCK)
    finally:
      webhook._post = saved_post
    check(len(posted) == 1 and "(Instance right)" in posted[0]["title"],
          f"a real stop notification carries the name: "
          f"{posted[0]['title'] if posted else '(nothing posted)'!r}")

    # Every title, not just the stop ones. The rest were call-time f-strings and so were
    # merely positional rather than constant, which is still wrong once names exist.
    source = io.open("utils/webhook.py", encoding="utf-8").read()
    check("{bot.instance}" not in source,
          "no title still interpolates the positional number")
    check(source.count("(Instance ") == 1,
          f"one place knows how the marker is spelled, got {source.count('(Instance ')}")
  finally:
    bot.instance_name, bot.hotkey = saved


def flag_cases():
  print("\nThe flags themselves:")
  for flag in ("--instance", "--port", "--hotkey"):
    check(any(flag in action.option_strings for action in cli.parser._actions),
          f"{flag} is accepted")
  # parse_known_args, so an unknown flag does not take the process down with it.
  parsed, _ = cli.parser.parse_known_args(["--instance", "left", "--port", "8042",
                                           "--hotkey", "f4", "--not-a-real-flag"])
  check(parsed.instance == "left" and parsed.port == 8042 and parsed.hotkey == "f4",
        f"and they parse: {parsed.instance!r}, {parsed.port!r}, {parsed.hotkey!r}")
  bare, _ = cli.parser.parse_known_args([])
  check(bare.instance is None and bare.port is None and bare.hotkey is None,
        "absent, they are None rather than a default that would look declared")


def main():
  label_cases()
  directory_cases()
  marker_cases()
  flag_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("An instance is named, and its logs and notifications follow the name.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
