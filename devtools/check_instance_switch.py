"""Instance tabs: find the running instances, and switch the page between them.

Phase 2 of running instances from the web UI. The page on any instance lists the others
and switches to one by going to its own page, carrying the section and the light/dark
choice along. Deliberately not by re-pointing this page's requests at the other port:
this page holds one instance's unsaved state, and a save landing mid-switch would write
it into the other instance.

Discovery asks the ports rather than reading files, so a tab reflects what is running
now; an instance with a config file and no process shows as not running.

  py devtools/check_instance_switch.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
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


def probe_cases():
  print("\nAsking a port who is there:")
  import server.main as server

  class Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                              # noqa: N802
      if self.path == "/instance":
        body = json.dumps({"name": "beta", "declared": True, "port": None, "hotkey": "f2"})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())
      else:
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_):
      pass

  listener = HTTPServer(("127.0.0.1", 0), Handler)
  port = listener.server_address[1]
  thread = threading.Thread(target=listener.serve_forever, daemon=True)
  thread.start()
  try:
    found = server._probe_instance(port)
    check(found and found["name"] == "beta", "an instance answering on a port is found")
    check(found and found["port"] == port,
          "under the port that answered, not the None it reports before choosing one")
  finally:
    listener.shutdown()

  started = time.time()
  check(server._probe_instance(port) is None, "a port with nothing on it is None")
  check(time.time() - started < 2, "and says so quickly, not after a long timeout")

  class Old(BaseHTTPRequestHandler):
    def do_GET(self):                                              # noqa: N802
      self.send_response(404)
      self.end_headers()

    def log_message(self, *_):
      pass

  listener = HTTPServer(("127.0.0.1", 0), Old)
  threading.Thread(target=listener.serve_forever, daemon=True).start()
  try:
    check(server._probe_instance(listener.server_address[1]) is None,
          "an older instance without the route is not listed, rather than crashing the list")
  finally:
    listener.shutdown()


def listing_cases():
  print("\nThe list the tabs are drawn from:")
  import server.main as server

  folder = tempfile.mkdtemp(prefix="check_instance_switch_")
  saved = (server._probe_instance, core_config.INSTANCE_DIR, bot.instance_name, bot.port,
           bot.hotkey)
  probed = []
  answers = {
      8001: {"name": "beta", "declared": True, "hotkey": "f2", "device_id": "127.0.0.1:5565"},
      8003: {"name": "f4", "declared": False, "hotkey": "f4", "device_id": "127.0.0.1:5555"},
  }

  def fake_probe(port):
    probed.append(port)
    answer = answers.get(port)
    return dict(answer, port=port) if answer else None

  try:
    server._probe_instance = fake_probe
    core_config.INSTANCE_DIR = folder
    for name in ("alpha", "beta", "gamma"):
      with io.open(os.path.join(folder, f"{name}.json"), "w", encoding="utf-8") as handle:
        json.dump({}, handle)
    bot.instance_name, bot.port, bot.hotkey = "alpha", 8000, "f1"

    rows = server.live_instances()["instances"]
    names = [row["name"] for row in rows]
    check(8000 not in probed and len(probed) == 9,
          "every other instance port is asked, and not this one's own")
    check(names == ["f4", "alpha", "beta", "gamma"],
          f"the default instance first, then the rest by name: {names}")
    by_name = {row["name"]: row for row in rows}
    check(by_name["alpha"]["current"] and by_name["alpha"]["running"],
          "this instance is current")
    check(by_name["beta"]["running"] and not by_name["beta"]["current"]
          and by_name["beta"]["port"] == 8001, "a running one carries the port to switch to")
    check(names.count("beta") == 1,
          "an instance both running and configured is listed once, as running")
    check(not by_name["gamma"]["running"] and by_name["gamma"]["port"] is None,
          "a configured instance with no process is listed as not running, with no port")
    check(not by_name["f4"]["declared"], "the default instance is marked undeclared")
  finally:
    (server._probe_instance, core_config.INSTANCE_DIR, bot.instance_name, bot.port,
     bot.hotkey) = saved
    shutil.rmtree(folder, ignore_errors=True)


def ui_cases():
  print("\nThe page:")
  with io.open("web/src/components/InstanceBanner.tsx", encoding="utf-8") as handle:
    banner = handle.read()
  with io.open("web/src/App.tsx", encoding="utf-8") as handle:
    app = handle.read()

  body = banner[banner.index("const switchTo = useCallback("):]
  flush, navigate = body.find("await beforeSwitch();"), body.find("window.location.href = switchUrl(")
  check(0 <= flush < navigate, "a pending save is flushed before the page leaves")
  # A failed save neither strands the user nor loses the edit silently: the page asks, and
  # switching anyway is one click. It used to navigate from a `finally`, so a failed save
  # took the edit with it without a word, while switching presets asked (review of
  # 8f5ae63, 2026-09-23).
  asks = body[flush:navigate]
  check("} finally {" not in asks and "window.confirm(" in asks and "if (!saved" in asks,
        "and if that save fails, the page asks before leaving -- switching anyway stays "
        "possible, so the user is not stranded")
  check("beforeSwitch={() => autoSaveRef.current()}" in app,
        "the flush is the page's own auto-save")
  check("fetch(\"/instances/live\"" in banner and "window.setInterval(load, POLL_MS)" in banner,
        "the tabs are refreshed while the page is open")
  single = banner[banner.index("if (!instance.declared && others.length === 0) {"):]
  check('role="tablist"' not in single[:400],
        "a single, unnamed setup shows no tabs -- only the way to add a second instance")
  check('new URLSearchParams({ tab, theme: dark ? "dark" : "light" })' in banner,
        "the section and light/dark travel with the switch")
  check("useState<string>(() => arrival.tab ||" in app
        and 'arrival.theme === "dark"' in app,
        "and the arriving page opens on them")
  check('window.history.replaceState(null, "", window.location.pathname)' in app,
        "then drops them from the address bar, so a reload does not re-apply them")
  stopped = banner[banner.index("key={`stopped-${row.name}`}"):]
  check('aria-disabled="true"' in stopped[:400] and "switchTo(" not in stopped[:1500],
        "a stopped instance is shown, but not as something to switch to")
  with io.open("web/dist/app.js", encoding="utf-8") as handle:
    check("/instances/live" in handle.read(), "the built bundle has it")


def main():
  probe_cases()
  listing_cases()
  ui_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The page finds the running instances and switches between them.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
