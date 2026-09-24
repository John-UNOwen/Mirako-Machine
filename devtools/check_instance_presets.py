"""Presets shared between instances: saving one reaches every instance that runs it.

Phase 4 of running instances from the web UI. The preset bar is back, and a preset is a
linked set of settings rather than a template copied once. Before this, saving a preset
reached only the instance whose page saved it; another instance on the same preset kept
running the old settings while its own page -- which shows the preset -- displayed the new
ones, so the page and the bot disagreed.

What has to hold:

  * Saving a preset writes it into every instance whose `preset_id` is that preset, and
    into no other -- config.json included, which runs its own preset.
  * An instance's device settings survive any preset: they are never carried.
  * The running ones are asked to reload; the saving instance reloads itself.
  * The page says which instances run each preset before an edit reaches them.

Temporary folders throughout; the reload nudge goes to a real listening socket.

  py devtools/check_instance_presets.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bot as bot                                            # noqa: E402
import core.config as core_config                                 # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def read(path):
  with io.open(path, encoding="utf-8") as handle:
    return json.load(handle)


def write(path, data):
  with io.open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle)


def instance(preset, device, runs=1):
  return {"preset_id": preset, "device_id": device, "use_adb": True, "window_name": "W",
          "config_name": "old", "independent_training": {"max_runs": runs, "deck": 3}}


class Sandbox:
  def __init__(self, server):
    self.server = server
    self.folder = tempfile.mkdtemp(prefix="check_instance_presets_")
    self.instances = os.path.join(self.folder, "instances")
    self.presets = os.path.join(self.folder, "presets")
    os.makedirs(self.instances)
    os.makedirs(self.presets)
    self.default = os.path.join(self.folder, "config.json")

  def __enter__(self):
    s = self.server
    self.saved = (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, s.CONFIG_DIR,
                  list(s.CURRENT_CONFIGS), bot.instance_name, s._apply_saved_config,
                  s._tell_instances_to_reload)
    core_config.CONFIG_PATH = self.default
    core_config.INSTANCE_DIR = self.instances
    s.CONFIG_DIR = self.presets
    s.CURRENT_CONFIGS[:] = [{"id": "speed", "name": "Speed Build"}]
    self.reloaded_self = []
    self.told = []
    s._apply_saved_config = lambda: self.reloaded_self.append(True)
    s._tell_instances_to_reload = lambda names: self.told.append(list(names))
    return self

  def __exit__(self, *_):
    s = self.server
    (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, s.CONFIG_DIR, configs,
     bot.instance_name, s._apply_saved_config, s._tell_instances_to_reload) = self.saved
    s.CURRENT_CONFIGS[:] = configs
    shutil.rmtree(self.folder, ignore_errors=True)

  def own(self, name):
    return os.path.join(self.instances, f"{name}.json")


def write_through_cases():
  print("\nSaving a preset:")
  import server.main as server
  with Sandbox(server) as box:
    write(box.default, instance("default", "127.0.0.1:5555"))
    write(box.own("alpha"), instance("speed", "127.0.0.1:5565"))
    write(box.own("beta"), instance("speed", "127.0.0.1:5575"))
    write(box.own("gamma"), instance("stamina", "127.0.0.1:5585"))
    write(os.path.join(box.presets, "speed.json"), {"config_name": "Speed Build"})
    default_before = read(box.default)
    gamma_before = read(box.own("gamma"))

    bot.instance_name = "alpha"
    result = server.update_named_config("speed", {
        "config_name": "Speed Build", "independent_training": {"max_runs": 7},
        # A page can send Setup keys along; they must never reach an instance this way.
        "device_id": "127.0.0.1:9999", "window_name": "Wrong"})

    alpha, beta = read(box.own("alpha")), read(box.own("beta"))
    check(sorted(result["applied_to"]) == ["alpha", "beta"],
          f"it reaches both instances that run it: {result['applied_to']}")
    check(alpha["independent_training"]["max_runs"] == 7
          and beta["independent_training"]["max_runs"] == 7,
          "and both now run the saved setting")
    check(alpha["independent_training"]["deck"] == 3 and beta["independent_training"]["deck"] == 3,
          "what the save did not mention is kept, as a page save always keeps it")
    check(alpha["device_id"] == "127.0.0.1:5565" and beta["device_id"] == "127.0.0.1:5575"
          and beta["window_name"] == "W",
          "each keeps its own device and window, whatever the preset carried")
    check(read(box.own("gamma")) == gamma_before, "an instance on another preset is untouched")
    check(read(box.default) == default_before,
          "and so is config.json, which runs a preset of its own")
    check(box.reloaded_self == [True], "the saving instance reloads its own settings")
    check(box.told == [["beta"]], f"and the other is told to reload, not itself: {box.told}")

    box.told.clear()
    box.reloaded_self.clear()
    result = server.update_named_config("unused", {"config_name": "Nobody's"})
    check(result["applied_to"] == [] and not box.told and not box.reloaded_self,
          "a preset nobody runs changes no instance and asks nothing to reload")

  with Sandbox(server) as box:
    write(box.default, instance("speed", "127.0.0.1:5555"))
    write(os.path.join(box.presets, "speed.json"), {})
    bot.instance_name = "alpha"
    result = server.update_named_config("speed", {"independent_training": {"max_runs": 2}})
    check(result["applied_to"] == ["default"] and box.told == [["default"]],
          "the default instance is reached too when it runs the preset, and told by name")
    check(read(box.default)["device_id"] == "127.0.0.1:5555", "keeping its device as well")


def reload_nudge_cases():
  print("\nTelling running instances to reload:")
  import server.main as server
  hits = []

  class Handler(BaseHTTPRequestHandler):
    def do_POST(self):                                             # noqa: N802
      hits.append((self.server.server_address[1], self.path, self.headers.get("Origin")))
      self.send_response(200)
      self.end_headers()
      self.wfile.write(b"{}")

    def log_message(self, *_):
      pass

  listeners = [HTTPServer(("127.0.0.1", 0), Handler) for _ in range(3)]
  for listener in listeners:
    threading.Thread(target=listener.serve_forever, daemon=True).start()
  beta_port, gamma_port, delta_port = (listener.server_address[1] for listener in listeners)
  saved = server.live_instances
  try:
    # gamma is running but was not named; delta was named but is not running, and still
    # carries a port something answers on -- the case that shows "running" is checked.
    server.live_instances = lambda: {"instances": [
        {"name": "alpha", "declared": True, "current": True, "running": True, "port": 1},
        {"name": "beta", "declared": True, "current": False, "running": True, "port": beta_port},
        {"name": "gamma", "declared": True, "current": False, "running": True, "port": gamma_port},
        {"name": "delta", "declared": True, "current": False, "running": False, "port": delta_port},
    ]}
    server._tell_instances_to_reload(["alpha", "beta", "delta"])
    check(hits == [(beta_port, "/config/reload", None)],
          f"only a running, named, other instance is asked, on its own port: {hits}")
    check(hits and hits[0][2] is None,
          "sent with no Origin, so the cross-site guard lets one instance ask another")
  finally:
    server.live_instances = saved
    for listener in listeners:
      listener.shutdown()

  saved_apply = server._apply_saved_config
  calls = []
  try:
    server._apply_saved_config = lambda: calls.append(True)
    check(server.reload_config_route()["status"] == "success" and calls == [True],
          "the reload route re-reads this instance's config")
  finally:
    server._apply_saved_config = saved_apply


def instance_route_cases():
  print("\nWhich preset each tab runs:")
  import server.main as server
  with Sandbox(server) as box:
    bot.instance_name = "alpha"
    write(box.own("alpha"), instance("speed", "127.0.0.1:5565"))
    write(box.own("gamma"), instance("stamina", "127.0.0.1:5585"))
    info = server.get_instance()
    check(info["preset_id"] == "speed" and info["preset_name"] == "Speed Build",
          "an instance reports its preset, by the name the preset list shows")
    saved_probe = server._probe_instance
    try:
      server._probe_instance = lambda port: None
      rows = {row["name"]: row for row in server.live_instances()["instances"]}
      check(rows["gamma"]["preset_id"] == "stamina",
            "and a stopped instance's preset is read from its file, so its tab can show it")
    finally:
      server._probe_instance = saved_probe


def ui_cases():
  print("\nThe page:")
  with io.open("web/src/App.tsx", encoding="utf-8") as handle:
    app = handle.read()
  with io.open("web/src/components/InstanceBanner.tsx", encoding="utf-8") as handle:
    banner = handle.read()
  check("const SHOW_PRESET_PICKER: boolean = true;" in app, "the preset bar is back")
  check("Set-up values are global" not in app,
        "its tooltip no longer says Setup values are shared, which phase 1 ended")
  check("usersOf(instances, preset.id)" in app,
        "each preset in the list names the other instances that run it")
  check("Also runs on {usersOf(instances, activeConfigId)" in app,
        "and the open preset says so beside its name, before an edit reaches them")
  delete = app[app.index("const users = usersOf(instances, activeConfigId);"):]
  check("still ${users.length === 1" in delete[:400],
        "deleting a preset another instance runs says so in the confirmation")
  check("{usersOf(instances, preset.id).length > 0 && (" in app,
        "the label naming them is drawn only when there are some")
  check("presetLabel(row)" in banner, "each tab shows the preset it runs")
  helper_cases()
  with io.open("web/dist/app.js", encoding="utf-8") as handle:
    check("Also runs on" in handle.read(), "the built bundle has it")


HELPER_SCRIPT = r"""
import { presetLabel, tabLabel, usersOf } from "./instances.ts";
const rows = [
  { name: "f1", declared: false, current: false, preset_id: "speed" },
  { name: "alpha", declared: true, current: true, preset_id: "speed" },
  { name: "beta", declared: true, current: false, preset_id: "speed" },
  { name: "gamma", declared: true, current: false, preset_id: "stamina", preset_name: "Stamina Build" },
];
console.log(JSON.stringify({
  speed: usersOf(rows, "speed"),
  stamina: usersOf(rows, "stamina"),
  none: usersOf(rows, "mile"),
  empty: usersOf(rows, ""),
  label: presetLabel(rows[3]),
  fallback: presetLabel(rows[2]),
  unnamed: tabLabel(rows[0]),
}));
"""


def helper_cases():
  import shutil as sh
  import subprocess
  node = sh.which("node")
  if not node:
    check(False, "node is on the PATH, which running the helpers needs")
    return
  folder = tempfile.mkdtemp(prefix="check_instance_presets_node_")
  try:
    sh.copy(os.path.join("web", "src", "lib", "instances.ts"), folder)
    script = os.path.join(folder, "run.mts")
    with io.open(script, "w", encoding="utf-8") as handle:
      handle.write(HELPER_SCRIPT)
    result = subprocess.run([node, "--experimental-strip-types", "--no-warnings", script],
                            capture_output=True, text=True, encoding="utf-8")
  finally:
    sh.rmtree(folder, ignore_errors=True)
  if result.returncode != 0:
    check(False, f"the helpers run under node: {result.stderr.strip()[:200]}")
    return
  got = json.loads(result.stdout.strip().splitlines()[-1])
  check(got["speed"] == ["Default", "beta"],
        f"a preset's other users leave out this page's own instance: {got['speed']}")
  check(got["stamina"] == ["gamma"] and got["none"] == [] and got["empty"] == [],
        "and name only instances on that preset -- none for a preset nobody runs")
  check(got["unnamed"] == "Default", "the unnamed instance is called Default, not its hotkey")
  check(got["label"] == "Stamina Build" and got["fallback"] == "speed",
        "a tab shows the preset's name, or its id when the name is not known")


def main():
  write_through_cases()
  reload_nudge_cases()
  instance_route_cases()
  ui_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("A preset reaches every instance that runs it, and the page says which those are.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
