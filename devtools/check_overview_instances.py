"""The Overview shows every emulator, and can only drive its own.

Phase 5f. There is no supervisor and no talking between processes: each instance writes
its queue to `stats/<device>/schedule.json` and its claim to `owner.json`, and an Overview
is a directory listing plus two reads. It degrades the way it should -- an instance that is
not running has a stale file, which is exactly what an Overview should show.

**The half that matters is `local`.** Run-now and clear-hold act on *this* process's
scheduler. Rendered on another instance's row they would quietly drive the wrong emulator
while appearing to drive that one, and the only thing between the user and that is which
row the button is in. So the flag is checked here, and so is the UI's use of it.

**A remote row is built from cooldowns alone.** Only this process knows its own task
order, which tasks are switched off, and which one it is inside right now -- all three are
in memory, not in the file. Claiming any of that for another instance would be inventing
it, so a remote row reports what its file honestly contains.

  py devtools/check_overview_instances.py
"""

import ast
import io
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as config                                      # noqa: E402

config.reload_config()

import server.main as server                                      # noqa: E402

# The task order and the enabled flags come from the bot, registered at startup. Without
# them `_queue` falls back to naming whatever the file holds -- which is what a remote row
# does anyway, so a remote row built with `live=True` would look identical and the case
# that matters would pass while proving nothing.
server.register_bot_control(tasks=lambda: [
    {"name": "career", "enabled": True},
    {"name": "team_trials", "enabled": True},
    {"name": "missions", "enabled": False},
])

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def make_instance(root, device, tasks=None, owner=None):
  folder = os.path.join(root, device)
  os.makedirs(folder, exist_ok=True)
  if tasks is not None:
    with io.open(os.path.join(folder, "schedule.json"), "w", encoding="utf-8") as handle:
      json.dump({"tasks": tasks}, handle)
  if owner is not None:
    with io.open(os.path.join(folder, "owner.json"), "w", encoding="utf-8") as handle:
      json.dump(owner, handle)
  return folder


def rows_cases():
  print("\nA row per emulator:")
  saved = (bot.use_adb, bot.device_id)
  tmp = tempfile.mkdtemp(prefix="check_overview_")
  try:
    bot.use_adb, bot.device_id = True, "127.0.0.1:5555"
    make_instance(tmp, "127.0.0.1_5555", tasks={})
    make_instance(tmp, "127.0.0.1_5565",
                  tasks={"team_trials": {"next_run": time.time() + 1800,
                                         "reason": "no charges"}},
                  owner={"instance": "right", "pid": 999999, "since": time.time()})
    # Neither a queue nor a claim: a leftover folder, not an instance.
    os.makedirs(os.path.join(tmp, "127.0.0.1_9999"), exist_ok=True)

    rows = server._instances(tmp)
    check(len(rows) == 2, f"two instances, and the empty folder is not one: {len(rows)}")
    check(rows[0]["local"] is True and rows[0]["device"] == "127.0.0.1_5555",
          f"this process's own emulator comes first and is marked local: {rows[0]['device']}")
    check(all(not row["local"] for row in rows[1:]),
          "and nothing else claims to be local")

    remote = rows[1]
    check(remote["name"] == "right",
          f"a remote row is named by its own claim, not its directory: {remote['name']}")
    check([t["name"] for t in remote["queue"]] == ["team_trials"],
          f"its queue is what its file holds, not this instance's task list: "
          f"{[t['name'] for t in remote['queue']]}")
    check(remote["queue"][0]["state"] == "waiting" and remote["queue"][0]["seconds"] > 1700,
          f"with the cooldown it recorded: {remote['queue'][0]}")
    check(remote["queue"][0]["reason"] == "no charges",
          "and the reason, which is the answer to why it is not racing")
    # A dead pid is a stopped instance, and that is knowable; the check must not report
    # it as running just because a claim file exists.
    check(remote["running"] is False,
          f"a claim from a process that is gone reads as stopped, got {remote['running']}")
  finally:
    bot.use_adb, bot.device_id = saved
    shutil.rmtree(tmp, ignore_errors=True)


def honesty_cases():
  """A remote row must not claim knowledge this process does not have."""
  print("\nWhat a remote row does not pretend to know:")
  saved = (bot.use_adb, bot.device_id, bot.is_bot_running)
  tmp = tempfile.mkdtemp(prefix="check_overview_")
  try:
    bot.use_adb, bot.device_id = True, "127.0.0.1:5555"
    bot.is_bot_running = True
    make_instance(tmp, "127.0.0.1_5555", tasks={})
    make_instance(tmp, "127.0.0.1_5565", tasks={"career": {"next_run": 0}},
                  owner={"instance": "right", "pid": 999999})

    rows = server._instances(tmp)
    remote = next(row for row in rows if not row["local"])
    states = {task["state"] for task in remote["queue"]}
    # "running" comes from entered_task(), which is this process's memory. Reporting it
    # for another instance would be reporting our own task under their name.
    check("running" not in states,
          f"no remote task is reported as running, got {states}")
    check("off" not in states,
          f"nor as switched off -- that lives in a config this process does not read: "
          f"{states}")

    local = next(row for row in rows if row["local"])
    check(len(local["queue"]) >= len(remote["queue"]),
          "while the local row still gets the full task list from the bot itself")
  finally:
    bot.use_adb, bot.device_id, bot.is_bot_running = saved
    shutil.rmtree(tmp, ignore_errors=True)


def control_cases():
  """The buttons must be gated on `local`, in the file that renders them."""
  print("\nControls stay with the instance that owns them:")
  source = io.open("web/src/components/OverviewSection.tsx", encoding="utf-8").read()
  check("instance.local &&" in source,
        "the Overview gates something on which row is this instance's")
  check('task.state === "waiting" && instance.local &&' in source,
        "Run now is offered only on the local row -- it posts to this server's scheduler")
  check("{instance.local && (" in source,
        "and so is Clear, which lifts this instance's hold and nobody else's")
  check("another instance" in source,
        "a remote row says what it is, so a missing button reads as deliberate")
  check("instance.running === null" in source,
        "unknown liveness is shown as unknown rather than as stopped")


def startup_cases():
  """The server has to know which device it is before the bot is started."""
  print("\nThe device is known from the start:")
  tree = ast.parse(io.open("main.py", encoding="utf-8").read())
  names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
  check("resolve_device" in names, "resolving the device is its own function")
  # Called at import-time startup as well as from main(). Every state file is keyed on
  # the device, so before this the server read `desktop`'s -- it showed an empty queue for
  # itself and listed the real emulator as another instance.
  calls = [node for node in ast.walk(tree)
           if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "resolve_device"]
  check(len(calls) >= 2,
        f"and it is called from startup as well as from main(), got {len(calls)} call(s)")


def main():
  rows_cases()
  honesty_cases()
  control_cases()
  startup_cases()
  print()
  if failures:
    print(f"{len(failures)} check(s) failed.")
    return 1
  print("Every emulator gets a row, and only this one gets buttons.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
