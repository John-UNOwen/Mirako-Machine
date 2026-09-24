"""The web server's file routes stay inside their folders, and foreign pages cannot write.

A review on 2026-09-17 found, and this confirmed over real HTTP:

  * /data/{path}  served any readable file: "../config.json", its URL-encoded forms, and
    on Windows an absolute path, "C:/Windows/win.ini". The catch-all that serves the web
    UI did the same.
  * /configs/{name} and /theme/{name} took "..%5cconfig" -- a backslash inside one URL
    segment -- to the live config.json. For reading, and through PUT, for writing.
  * POST /theme/{name} wrote the wrong thing: a module-level `data`, which is whatever
    preset the startup loop loaded last, rather than the theme it was sent.
  * Renaming a preset updated the file but not the list GET /configs serves.
  * Duplicating stacked " (Copy)" on names that already had it, and did not write the
    new name into the copy, so a restart brought it back under its source's name.
  * Body-less POSTs are "simple" requests a browser sends cross-site without asking, so
    any open page could start or stop the bot. CORS only hid the reply.

The path and origin cases need a real server -- the test client normalises exactly the
paths that matter away -- so one is started on a spare port and spoken to over a raw
socket. Nothing in here writes to config.json even when the guards are broken: the
traversal writes aim at a throwaway sentinel file instead.

  py devtools/check_server_hardening.py
"""

import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []
THEME = "__hardening_check"


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def md5(path):
  with open(path, "rb") as handle:
    return hashlib.md5(handle.read()).hexdigest()


def free_port():
  with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    return probe.getsockname()[1]


class Server:
  def __init__(self):
    self.port = free_port()
    self.process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1",
         "--port", str(self.port), "--log-level", "warning"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    deadline = time.time() + 60
    while time.time() < deadline:
      try:
        if self.request("GET", "/data/skills.json")[0] == 200:
          return
      except OSError:
        pass
      time.sleep(0.5)
    raise SystemExit("the server did not come up")

  def request(self, method, path, body=None, headers=None):
    """Sent byte for byte, so the path reaches the server exactly as written."""
    payload = json.dumps(body).encode() if body is not None else b""
    lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{self.port}",
             "Connection: close", f"Content-Length: {len(payload)}"]
    if body is not None:
      lines.append("Content-Type: application/json")
    for key, value in (headers or {}).items():
      lines.append(f"{key}: {value}")
    with socket.create_connection(("127.0.0.1", self.port), timeout=30) as connection:
      connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + payload)
      data = b""
      while True:
        chunk = connection.recv(65536)
        if not chunk:
          break
        data += chunk
    head, _, content = data.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), content

  def stop(self):
    self.process.kill()
    self.process.wait(timeout=10)


def path_cases(server):
  print("\nFile routes stay inside their folders:")
  status, content = server.request("GET", "/data/skills.json")
  check(status == 200 and content[:1] in (b"[", b"{"), "a real data file is still served")
  status, _ = server.request("GET", "/")
  check(status == 200, "and so is the web UI")
  bundle = next((name for name in os.listdir("web/dist") if name.endswith(".js")), None)
  if bundle:
    check(server.request("GET", f"/{bundle}")[0] == 200, f"and its {bundle}")

  attempts = [
      ("GET", "/data/../config.json"), ("GET", "/data/%2e%2e/config.json"),
      ("GET", "/data/..%2fconfig.json"), ("GET", "/data/..%5cconfig.json"),
      ("GET", "/data/C:/Windows/win.ini"), ("GET", "/data/%2e%2e/%2e%2e/%2e%2e/Windows/win.ini"),
      ("GET", "/..%5cconfig.json"), ("GET", "/%2e%2e/config.json"),
      ("GET", "/C:/Windows/win.ini"),
      ("GET", "/configs/..%5cconfig"), ("GET", "/theme/..%5cconfig"),
      ("POST", "/configs/..%5cconfig/duplicate"),
  ]
  for method, path in attempts:
    status, content = server.request(method, path)
    leaked = b"device_id" in content or b"[fonts]" in content.lower()
    check(status == 404 and not leaked, f"{method} {path} -> {status}")

  # A name is a file in its folder, not a path: "instances\x" would stay inside config/
  # and still reach a per-instance config the preset list knows nothing about.
  nested = os.path.join("themes", "__hardening_nested")
  os.makedirs(nested, exist_ok=True)
  with open(os.path.join(nested, "inner.json"), "w", encoding="utf-8") as handle:
    json.dump({"nested": True}, handle)
  try:
    status, _ = server.request("GET", "/theme/__hardening_nested%5cinner")
    check(status == 404, f"a name reaching into a subfolder is refused ({status})")
  finally:
    shutil.rmtree(nested, ignore_errors=True)

  # The writes aim at a sentinel beside this script rather than at config.json: if the
  # guard is ever broken -- the mutation run breaks it on purpose -- these requests
  # really write, and a running bot must not have its config rewritten underneath it.
  sentinel = os.path.join("devtools", "__hardening_sentinel.json")
  with open(sentinel, "w", encoding="utf-8") as handle:
    json.dump({"untouched": True}, handle)
  try:
    before = md5(sentinel)
    status, _ = server.request("PUT", "/configs/..%5cdevtools%5c__hardening_sentinel",
                               body={"config_name": "overwritten"})
    check(status == 404 and md5(sentinel) == before,
          f"PUT through a preset name is refused ({status}) and the file is untouched")
    status, _ = server.request("POST", "/theme/..%5cdevtools%5c__hardening_sentinel",
                               body={"overwritten": True})
    check(status == 404 and md5(sentinel) == before,
          f"and a theme cannot be written outside themes/ ({status})")
  finally:
    os.remove(sentinel)


def origin_cases(server):
  print("\nWrites from other sites:")
  theme_path = os.path.join("themes", f"{THEME}.json")
  before = md5("config.json")
  try:
    for headers, label in (({"Origin": "http://evil.example"}, "another site's Origin"),
                           ({"Origin": "null"}, "a sandboxed page's null Origin"),
                           ({"Sec-Fetch-Site": "cross-site"}, "a cross-site fetch with no Origin")):
      status, _ = server.request("POST", f"/theme/{THEME}", body={"from": "evil"}, headers=headers)
      check(status == 403 and not os.path.exists(theme_path),
            f"{label} is refused before the handler runs ({status})")
    # Only routes that do nothing harmful if the guard were missing: this server is a
    # separate process with no bot in it, and the preset does not exist. /stats/reset and
    # /hold/clear are left out on purpose -- with the guard broken, as the mutation run
    # breaks it, they would act on the real stats and schedule files.
    for path in ("/bot/stop", "/configs/__no_such_preset/duplicate"):
      status, _ = server.request("POST", path, headers={"Origin": "https://evil.example"})
      check(status == 403, f"POST {path} from another site -> {status}")
    status, _ = server.request("GET", "/data/skills.json", headers={"Origin": "http://evil.example"})
    check(status == 200, "reads are not refused -- CORS already keeps their answers private")

    theme = {"id": THEME, "label": "Hardening check", "primary": "#123456"}
    status, content = server.request("POST", f"/theme/{THEME}", body=theme,
                                     headers={"Origin": f"http://127.0.0.1:{server.port}"})
    check(status == 200, f"the web UI's own origin can still write ({status})")
    saved = None
    if os.path.exists(theme_path):
      with open(theme_path, encoding="utf-8") as handle:
        saved = json.load(handle)
    check(saved == theme, "and a saved theme is the theme it was sent, not a config preset")
    status, _ = server.request("POST", f"/theme/{THEME}", body=theme,
                               headers={"Origin": "http://localhost:5173"})
    check(status == 200, "as can the dev server on localhost")
    status, _ = server.request("POST", f"/theme/{THEME}", body=theme)
    check(status == 200, "and a script sending no Origin at all")
    check(md5("config.json") == before, "config.json was never touched")
  finally:
    if os.path.exists(theme_path):
      os.remove(theme_path)


def preset_cases():
  print("\nPresets:")
  import server.main as server
  saved = (server.CONFIG_DIR, list(server.CURRENT_CONFIGS))
  folder = tempfile.mkdtemp()
  try:
    server.CONFIG_DIR = folder
    server.CURRENT_CONFIGS.clear()
    with open(os.path.join(folder, "config_1.json"), "w", encoding="utf-8") as handle:
      json.dump({"config_name": "Speed Build"}, handle)
    server.add_config_to_global_list({"id": "config_1", "name": "Speed Build"})

    first = server.duplicate_named_config("config_1")["config"]
    check(first["name"] == "Speed Build (Copy)", f"a copy is named {first['name']!r}")
    with open(os.path.join(folder, f"{first['id']}.json"), encoding="utf-8") as handle:
      check(json.load(handle).get("config_name") == "Speed Build (Copy)",
            "and the copy's file says so, so a restart keeps the name")
    second = server.duplicate_named_config(first["id"])["config"]
    check(second["name"] == "Speed Build (Copy)",
          f"copying a copy does not stack the suffix ({second['name']!r})")
    check(server.copy_name("Config (Copy) (Copy)") == "Config (Copy)"
          and server.copy_name("") == "Config (Copy)",
          "an already-stacked name collapses to one, and a nameless one gets a name")

    server.update_named_config("config_1", {"config_name": "Stamina Build"})
    listed = {cfg["id"]: cfg["name"] for cfg in server.get_configs()["configs"]}
    check(listed.get("config_1") == "Stamina Build",
          f"a rename shows in GET /configs straight away ({listed.get('config_1')!r})")
  finally:
    server.CONFIG_DIR = saved[0]
    server.CURRENT_CONFIGS[:] = saved[1]
    shutil.rmtree(folder, ignore_errors=True)


def other_cases():
  print("\nThe rest:")
  import utils.device_action_wrapper as device_action
  try:
    cache = device_action.cache_templates({"missing": "assets/does/not/exist.png"})
    check(cache == {}, "a missing template is skipped with a warning, not a crash")
  except Exception as error:                                       # noqa: BLE001
    check(False, f"a missing template crashes cache_templates: {error}")


def main():
  server = Server()
  try:
    path_cases(server)
    origin_cases(server)
  finally:
    server.stop()
  preset_cases()
  other_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The server keeps to its folders and refuses writes from other sites.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
