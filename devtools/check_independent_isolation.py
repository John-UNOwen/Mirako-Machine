"""Enforce that Independent Training only depends on the shared substrate.

Originally this kept the option of splitting the mode out cheap: as long as nothing here
reached into normal-career code, "fork it" stayed a matter of deleting the drop list
rather than untangling it. That deletion has since happened, so the guarantee is now the
other way round -- what it protects is the small dependency footprint the mode ended up
with, against the next convenient import that would grow it back.

The forbidden list is short now because most of what it named no longer exists. The
module count it prints is the more useful half: if that number climbs, something new got
pulled in.

Usage:
  py devtools/check_independent_isolation.py
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The Independent Training feature itself.
FEATURE_MODULES = [
  "scenarios/independent_training.py",
  "scenarios/independent_screens.py",
  "core/independent_skill.py",
  "core/independent_borrow.py",
  "core/independent_stats.py",
]

# What is left to forbid. The career modules that filled this list were deleted, and so
# was the standalone races tool (auto_misc, removed in 1.0.2). Kept here so that if it
# comes back it still cannot become one of this mode's dependencies.
FORBIDDEN = {
  "auto_misc",
}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def module_name_for(relative_path):
  return relative_path[:-3].replace("/", ".").replace("\\", ".")


def path_for(module):
  candidate = os.path.join(PROJECT_ROOT, module.replace(".", os.sep) + ".py")
  return candidate if os.path.isfile(candidate) else None


def imports_of(file_path):
  """First-party modules imported by a file."""
  with open(file_path, "r", encoding="utf-8") as handle:
    tree = ast.parse(handle.read(), filename=file_path)

  found = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      for alias in node.names:
        found.add(alias.name)
    elif isinstance(node, ast.ImportFrom):
      if node.module and node.level == 0:
        found.add(node.module)
  return {name for name in found if path_for(name)}


def main():
  violations = []
  visited = set()
  # module -> how the feature reaches it, for a useful error message
  queue = [(module_name_for(path), [module_name_for(path)]) for path in FEATURE_MODULES]

  for relative in FEATURE_MODULES:
    if not os.path.isfile(os.path.join(PROJECT_ROOT, relative)):
      print(f"FAIL: {relative} does not exist")
      return 1

  while queue:
    module, chain = queue.pop()
    if module in visited:
      continue
    visited.add(module)

    file_path = path_for(module)
    if file_path is None:
      continue

    for imported in sorted(imports_of(file_path)):
      if imported in FORBIDDEN:
        violations.append(" -> ".join(chain + [imported]))
      elif imported not in visited:
        queue.append((imported, chain + [imported]))

  reachable = sorted(visited)
  print(f"Independent Training reaches {len(reachable)} first-party module(s):")
  for module in reachable:
    print(f"  {module}")

  if violations:
    print(f"\n{len(violations)} FORBIDDEN DEPENDENCY(IES):")
    for violation in violations:
      print(f"  - {violation}")
    print("\nIndependent Training must not depend on normal-career modules. If a helper "
          "is genuinely needed, copy it (as is done with is_skill_match) rather than "
          "importing it.")
    return 1

  print("\nNo normal-career dependencies. This mode is still cleanly separable.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
