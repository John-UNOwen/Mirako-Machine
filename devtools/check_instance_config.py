"""Which config an instance reads, and what is layered over it.

Phase 5c. Each instance owns a whole config under `config/instances/`, and
`config/machine.json` owns the handful of settings that belong to the box rather than to
an emulator. The overlay this replaced -- one shared base plus a thin per-instance diff --
would have meant a key an instance did not override silently following the other one's
value, which is a bad way to find out your second account is borrowing the first's cards.

Three things are checked.

**That nothing moved for a single instance.** No `--instance`, no `machine.json`, and the
path is `config.json` and the contents are byte-identical to reading that file directly.
This is the whole promise of the phase and it is the first thing here.

**The layering.** Template underneath, so a key missing from a file takes a default rather
than raising -- `reload_config` reads with bare subscripts, so an absent key used to end
the process, which was survivable with one config to maintain and is not with one per
instance. Then the file. Then `machine.json` on top, for the keys it owns.

**That a disagreement is reported rather than absorbed.** A machine-wide setting still
sitting in an instance file is not an error, but it is dead text, and silently overruling
it is how someone spends an afternoon editing a value that nothing reads.

Run against temporary files, so it never touches the real config.

  py devtools/check_instance_config.py
"""

import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def write(path, data):
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with io.open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle)


def unchanged_cases():
  """A single instance must read exactly what it read before any of this existed."""
  print("\nThe single-instance promise:")
  saved = bot.instance_name
  bot.instance_name = ""
  try:
    check(config.config_path() == "config.json",
          f"undeclared, the path is config.json, got {config.config_path()!r}")
    with io.open("config.json", encoding="utf-8") as handle:
      direct = json.load(handle)
    check(config.load_config() == direct,
          "and the layered load returns exactly what reading that file returns -- the "
          "template fills nothing because a complete config has nothing to fill")
  finally:
    bot.instance_name = saved


def path_cases():
  print("\nWhich file an instance reads:")
  saved = bot.instance_name
  try:
    bot.instance_name = "right"
    expected = os.path.join("config", "instances", "right.json")
    check(config.config_path() == expected,
          f"declared, it reads its own file, got {config.config_path()!r}")
    bot.instance_name = "left"
    check(config.config_path().endswith(os.path.join("instances", "left.json")),
          "and a different name reads a different file")
    # Resolved per call, not captured at import. The name is set from the command line
    # after every module is imported, so a constant would answer for whoever imported
    # first -- the mistake the Discord marker made for the life of every process.
    bot.instance_name = ""
    check(config.config_path() == "config.json",
          "clearing the name goes back to config.json, so nothing cached the answer")
  finally:
    bot.instance_name = saved


def layer_cases():
  print("\nThe layers:")
  saved_template, saved_machine = config.TEMPLATE_PATH, config.MACHINE_PATH
  tmp = tempfile.mkdtemp(prefix="check_instance_config_")
  try:
    config.TEMPLATE_PATH = os.path.join(tmp, "template.json")
    config.MACHINE_PATH = os.path.join(tmp, "machine.json")
    own = os.path.join(tmp, "own.json")

    write(config.TEMPLATE_PATH, {"kept": "template", "filled": "from template",
                                 "nested": {"a": 1, "b": 2}, "listy": [1, 2, 3]})
    write(own, {"kept": "own", "nested": {"a": 99}, "listy": [7]})

    loaded = config.load_config(own)
    check(loaded["kept"] == "own", "the file beats the template where both speak")
    check(loaded["filled"] == "from template",
          "a key the file omits takes the template's value instead of raising")
    check(loaded["nested"] == {"a": 99, "b": 2},
          f"nested dicts merge key by key, got {loaded['nested']}")
    # A list is a whole answer, not a starting point: merging "the skills I want" with a
    # default list would hand back a set nobody asked for.
    check(loaded["listy"] == [7], f"a list replaces rather than extends, got {loaded['listy']}")

    write(config.MACHINE_PATH, {"kept": "machine", "nested": {"a": 5}})
    loaded = config.load_config(own)
    check(loaded["kept"] == "machine", "machine.json beats the instance file")
    check(loaded["nested"]["a"] == 5 and loaded["nested"]["b"] == 2,
          f"and merges rather than replacing the block, got {loaded['nested']}")

    check(sorted(config.MACHINE_CONFLICTS) == ["kept", "nested.a"],
          f"both disagreements are named, got {config.MACHINE_CONFLICTS}")

    # Agreement is not a conflict; only a value that is actually being overruled is.
    write(own, {"kept": "machine", "nested": {"a": 5}})
    config.load_config(own)
    check(config.MACHINE_CONFLICTS == [],
          f"a file that agrees reports nothing, got {config.MACHINE_CONFLICTS}")

    os.remove(config.MACHINE_PATH)
    config.load_config(own)
    check(config.MACHINE_CONFLICTS == [],
          "and with no machine.json there is nothing to disagree with")

    missing = os.path.join(tmp, "not-there.json")
    try:
      config.load_config(missing)
      check(False, "a missing config raises rather than loading an empty one")
    except RuntimeError as exception:
      check("not-there.json" in str(exception),
            f"a missing config names itself in the error, got {exception}")
  finally:
    config.TEMPLATE_PATH, config.MACHINE_PATH = saved_template, saved_machine
    shutil.rmtree(tmp, ignore_errors=True)


def server_cases():
  """The UI has to write the same file the bot reads, or a setting saves into the void."""
  print("\nThe server reads the same file:")
  source = io.open("server/main.py", encoding="utf-8").read()
  check("open(CONFIG_PATH" not in source,
        "no endpoint still opens the hardcoded config.json")
  # Counted as uses of config_path(), not as open() calls: the active config is now
  # written through _write_json_object (atomically), which resolves the same way but is
  # not an open(). What must never come back is a path that is not config_path().
  check(source.count("core_config.config_path()") >= 5,
        f"every config access resolves per instance, got "
        f"{source.count('core_config.config_path()')}")


def main():
  unchanged_cases()
  path_cases()
  layer_cases()
  server_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("An instance reads its own config, and a single instance reads what it always did.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
