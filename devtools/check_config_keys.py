"""Every config setting the code reads is one the config actually loads.

A setting has to appear in five places before it works, and the fifth is the quiet one.
`config.template.json`, `config.json` and `config/default.json` carry the value; the Zod
schema and a control put it in the UI. None of that makes it readable at runtime --
`core/config.reload_config` has to copy it onto the config module, and until it does
every `getattr(config, "NAME", default)` in the codebase silently returns the default.

That is not a hypothetical. The daily-race settings shipped with all four of the other
places done: the UI wrote them to disk, the Overview offered the toggle, and the task
could never turn on, because nothing read them back. It looked exactly like a UI bug.

This scans for the names the code reads and checks each one survives a reload.

  py devtools/check_config_keys.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config                                     # noqa: E402

failures = []

# Where settings are read. devtools are excluded: a tool that pokes at a name the runtime
# does not use is its own business.
SEARCHED = ("scenarios", "core", "utils", "server", "main.py")

# getattr(config, "NAME", ...) and config.NAME alike.
GETATTR = re.compile(r"getattr\(\s*config\s*,\s*[\"']([A-Z][A-Z0-9_]*)[\"']")
DIRECT = re.compile(r"\bconfig\.([A-Z][A-Z0-9_]*)\b")

# Names that are set somewhere other than reload_config, or are not settings at all.
EXEMPT = {
  # Assigned by main.py from the command line rather than read from the file.
  "USE_ADB", "DEVICE_ID",
}


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def python_files():
  for root in SEARCHED:
    if root.endswith(".py"):
      yield root
      continue
    for base, _, names in os.walk(root):
      if "__pycache__" in base:
        continue
      for name in names:
        if name.endswith(".py"):
          yield os.path.join(base, name)


def names_read():
  """Every config setting the runtime asks for, and one file that asks for it."""
  found = {}
  for path in python_files():
    with open(path, "r", encoding="utf-8") as handle:
      text = handle.read()
    for pattern in (GETATTR, DIRECT):
      for name in pattern.findall(text):
        found.setdefault(name, path)
  return found


def main():
  print("\nSettings the code reads:")
  config.reload_config()
  read = names_read()
  print(f"  {len(read)} name(s) across {len(SEARCHED)} tree(s)\n")

  missing = []
  for name, path in sorted(read.items()):
    if name in EXEMPT:
      continue
    if not hasattr(config, name):
      missing.append((name, path))

  for name, path in missing:
    print(f"  FAIL  {name} is read in {path} but reload_config never sets it")
    failures.append(name)
  check(not missing,
        f"every setting the code reads survives a reload ({len(read) - len(missing)} ok)")

  print()
  if failures:
    print(f"{len(missing)} setting(s) read but never loaded. Add them to "
          f"core/config.reload_config, or they will silently take their default.")
    return 1
  print("Every config name the runtime reads is one reload_config actually sets.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
