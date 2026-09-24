"""What the ADB test and a failed start say about which emulator the bot is on.

A green ADB test (connected and read a frame) is the moment the tested address becomes
the bot's device: it is persisted on the same path a Setup save uses -- the process's
own config file, plus the shared setup.json for the default instance; a named instance
writes its own file alone -- and the process keeps it instead of restoring the pre-test
value. A test that connected but could not read a screen, or could not connect at all,
saves nothing and says which device the bot still runs on: the saved one, or the
default when the saved value is empty. A config the save cannot parse is guarded, not
written, and the response says exactly what was and was not written -- it never says
Saved about an address nothing the bot reads carries, and no claim about the config's
ADB switch follows a reload that could not read the config. A start that could not
reach the device in ADB
mode said "Failed to focus Umamusume window" -- a window that was never looked for --
naming neither the device it tried nor the fact that the Test button tests the page's
value, not the config's; and an empty Device ID became the default address
127.0.0.1:5555 with only the plain "Connecting to ..." line, so with several emulators
up the user saw the bot drive whichever one sat on 5555 and had no way to know the
address was a fallback. A process pinned by --use-adb keeps driving the flag's address
whatever a test saves, so its reports say which address is which instead of claiming
the tested one.

The test and a start share one lock, core/bot.py's bot_state_lock: both read
is_bot_running once and then act on what they read, so without it a start landing
between the test's check and its device re-point began a run on fields the test was
overwriting -- and, since a green test persists its address, left the tested address
in the config and the shared file with the finally's restore skipped. The window is
microseconds and cannot be reproduced offline, so the serialization is asserted in
the source -- the way check_instance_refusals walks main's AST -- and the wait each
side does is exercised by holding the lock by hand, which holds it for exactly as
long as the window would be.

Fake adb throughout: the endpoint bodies and the real init_adb / _run_bot are driven
directly, and what the run said is captured from its own log output.

  py devtools/check_adb_device_truth.py
"""
import ast
import asyncio
import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                              # noqa: E402

import core.bot as bot                                          # noqa: E402
import core.config as core_config                               # noqa: E402
import main as main_module                                      # noqa: E402
import server.main as server                                    # noqa: E402
from utils import adb_actions                                   # noqa: E402

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def template():
  return json.loads(io.open("config.template.json", encoding="utf-8").read())


def write(path, data):
  io.open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2))


class Sandbox:
  """A config directory of its own, so nothing here touches the real one."""

  def __init__(self):
    self.folder = tempfile.mkdtemp(prefix="check_adb_truth_")
    self.config = os.path.join(self.folder, "config.json")

  def __enter__(self):
    self.saved = (core_config.CONFIG_PATH, core_config.INSTANCE_DIR,
                  core_config.MACHINE_PATH, server.GLOBAL_SETUP_PATH,
                  bot.instance_name)
    core_config.CONFIG_PATH = self.config
    core_config.INSTANCE_DIR = os.path.join(self.folder, "instances")
    os.makedirs(core_config.INSTANCE_DIR)
    # No machine.json in the sandbox: a machine-wide overlay would answer with values
    # the sandboxed config never set.
    core_config.MACHINE_PATH = os.path.join(self.folder, "no-machine.json")
    # A green test's save writes the default instance's shared file, so it must land
    # in the sandbox too: left unpatched, the suite would rewrite the repo's own
    # config/setup.json out from under itself.
    server.GLOBAL_SETUP_PATH = os.path.join(self.folder, "setup.json")
    bot.instance_name = ""
    return self

  def __exit__(self, *_):
    (core_config.CONFIG_PATH, core_config.INSTANCE_DIR, core_config.MACHINE_PATH,
     server.GLOBAL_SETUP_PATH, bot.instance_name) = self.saved
    shutil.rmtree(self.folder, ignore_errors=True)

  def write_config(self, device_id, use_adb=True):
    # device_id=None leaves the key out of the file entirely, so the template's
    # default stands in -- the other way a saved Device ID can be empty.
    document = template()
    document["use_adb"] = use_adb
    if device_id is None:
      document.pop("device_id", None)
    else:
      document["device_id"] = device_id
    io.open(self.config, "w", encoding="utf-8").write(json.dumps(document, indent=2))

  def read_config(self):
    return json.loads(io.open(self.config, encoding="utf-8").read())

  def shared(self):
    """The sandbox's shared setup file, as the server module sees it, or {}."""
    try:
      return json.loads(io.open(server.GLOBAL_SETUP_PATH, encoding="utf-8").read())
    except (OSError, ValueError):
      return {}


class FakeDevice:
  def __init__(self, serial):
    self.serial = serial

  def screenshot(self, **kwargs):
    # The size every coordinate in this repo assumes, so the test's report stays about
    # the device, not the resolution -- and with content on it, since the connect-time
    # check refuses a flat frame as a device that is rendering nothing at all.
    frame = np.zeros((1080, 800, 3), dtype=np.uint8)
    frame[::2, ::2] = 255
    return frame

  def shell(self, command, timeout=None):
    return ""


class FakeAdb:
  """The adbutils.adb module, faked: a device answers on the addresses it is known on."""

  def __init__(self, unreachable=()):
    self.unreachable = set(unreachable)
    self.known = set()
    self.connected = []

  def connect(self, address, timeout=None):
    self.connected.append(address)
    if address in self.unreachable:
      raise RuntimeError(f"no device found at {address}")
    self.known.add(address)

  def device(self, serial):
    if serial not in self.known:
      raise RuntimeError(f"device '{serial}' not found")
    return FakeDevice(serial)


@contextlib.contextmanager
def fake_adb(unreachable=()):
  """Point the real adb_actions module at a fake adb for the with-block's duration."""
  saved = (adb_actions.adb, adb_actions.device, adb_actions.cached_screenshot,
           adb_actions._warned_shape, adb_actions._warned_empty_region)
  fake = FakeAdb(unreachable)
  adb_actions.adb = fake
  adb_actions.device = None
  adb_actions.cached_screenshot = []
  adb_actions._warned_shape = False
  adb_actions._warned_empty_region = False
  try:
    yield fake
  finally:
    (adb_actions.adb, adb_actions.device, adb_actions.cached_screenshot,
     adb_actions._warned_shape, adb_actions._warned_empty_region) = saved


class Collector(logging.Handler):
  def __init__(self):
    super().__init__(level=logging.WARNING)
    self.records = []

  def emit(self, record):
    self.records.append((record.levelno, record.getMessage()))


@contextlib.contextmanager
def captured_output():
  """What the code under test said at warning and above, and nothing else."""
  root = logging.getLogger()
  saved = (root.handlers, root.level)
  collector = Collector()
  root.handlers = [collector]
  root.setLevel(logging.WARNING)
  try:
    yield collector
  finally:
    root.handlers, root.level = saved


class Request:
  """What the page hands the endpoint: its JSON body, as FastAPI would deliver it."""

  def __init__(self, body):
    self.body = body

  async def json(self):
    return self.body


def a_green_test_on_another_address_saves_it():
  print("A green test on an address the bot does not run on")
  saved = "127.0.0.1:5555"
  tested = "127.0.0.1:5565"
  with Sandbox() as box:
    box.write_config(saved)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": tested})))
        detail = result["detail"]
        check(result["status"] == "success", f"the test succeeds: {detail}")
        check(f"Saved: the bot will now run on '{tested}'." in detail,
              "and the green test makes the tested address the bot's device ...")
        check("Save the page" not in detail and "(the saved config), not" not in detail,
              "without the old note telling the user to save the page: the test saved it")
      check(box.read_config()["device_id"] == tested,
            "the address lands in the config the process runs on ...")
      check(box.shared().get("device_id") == tested,
            "... and in the shared file the default instance's page mirrors it from")
      check(bot.device_id == tested,
            "and the process keeps it: a test that saved is not restored away from the device it proved")
      check(bot.use_adb is True,
            "and on ADB, the switch the saved config carries")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot

  # The not-fully-green half: connected, but the screen could not be read. Nothing is
  # saved, and the report says which device the bot still runs on.
  with Sandbox() as box:
    box.write_config(saved)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        real_screenshot = adb_actions.screenshot

        def broken_screenshot(*args, **kwargs):
          raise RuntimeError("no frame")

        adb_actions.screenshot = broken_screenshot
        try:
          result = asyncio.run(server.test_adb(Request({"device_id": tested})))
        finally:
          adb_actions.screenshot = real_screenshot
        detail = result["detail"]
        # Reported, and reported as a failure: this used to expect "success", which the page
        # draws green, for a device the bot cannot read (review of 8f5ae63, 2026-09-23).
        check(result["status"] == "fail" and detail.startswith(f"Connected to '{tested}'"),
              f"a device that connects but cannot be screenshotted is reported, as a "
              f"failure: {result['status']}: {detail}")
        check(f"the bot runs on '{saved}' (the saved config), not '{tested}'" in detail,
              "and the same note goes on that report too")
        check("Save the page" not in detail,
              "without advising a page save either: a Use-triggered test runs while the "
              "page's own auto-save (~1s later) lands the new value, so the advice was "
              "stale and already carried out by the time it is read")
      check(box.read_config()["device_id"] == saved,
            "and a test it could not fully verify saves nothing: the config still says the saved address")
      check(bot.use_adb is False and bot.device_id == "",
            "and the process is restored to where the test found it")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_test_on_the_saved_address_writes_nothing():
  print("\nA green test on the address the bot runs on")
  addr = "127.0.0.1:5565"
  with Sandbox() as box:
    box.write_config(addr)
    # The shared file in agreement with the config, so "already" means nothing is
    # written anywhere, and the mtimes below can prove it.
    write(server.GLOBAL_SETUP_PATH, {"device_id": addr, "use_adb": True})
    own_mtime = os.path.getmtime(box.config)
    shared_mtime = os.path.getmtime(server.GLOBAL_SETUP_PATH)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": addr})))
        detail = result["detail"]
        check(result["status"] == "success" and detail.startswith(f"Connected to '{addr}'"),
              f"the test succeeds: {detail}")
        check(f"The bot already runs on '{addr}'." in detail and "Saved:" not in detail,
              "and it says the bot already runs on it, without saving again")
      check(box.read_config()["device_id"] == addr,
            "and re-testing the saved address leaves the config's content ...")
      check(os.path.getmtime(box.config) == own_mtime,
            "... and its mtime untouched ...")
      check(os.path.getmtime(server.GLOBAL_SETUP_PATH) == shared_mtime,
            "... as is the shared file's")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def an_empty_device_id_says_it_fell_back():
  print("\nAn empty Device ID, on connecting")
  saved = (bot.use_adb, bot.device_id)
  try:
    with fake_adb() as adb:
      bot.use_adb, bot.device_id = True, ""
      with captured_output() as said:
        ok = adb_actions.init_adb()
      check(ok is True, "the connection still succeeds ...")
      check(adb.connected == ["127.0.0.1:5555"],
            "... on the default address, which is what actually happened")
      check(any("127.0.0.1:5555" in msg and "empty" in msg.lower()
                for level, msg in said.records),
            "and it says so at the point the empty value becomes the default")
    with fake_adb() as adb:
      bot.use_adb, bot.device_id = True, "127.0.0.1:5565"
      with captured_output() as said:
        adb_actions.init_adb()
      check(said.records == [],
            "and a Device ID that was actually chosen is not warned about")
  finally:
    bot.use_adb, bot.device_id = saved


def a_run_resolved_from_an_empty_config_says_it_is_on_the_default():
  print("\nAn empty Device ID in the saved config, the path that actually happens")
  saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
  try:
    with Sandbox() as box:
      box.write_config("")
      # A fresh process starts on core.bot's seed value; the suite may have left
      # anything else in bot.device_id, so start from the seed it really has.
      bot.device_id = "127.0.0.1:5555"
      bot.is_bot_running = False
      # The real order of a run: reload the config, decide the device, connect.
      core_config.reload_config()
      main_module.resolve_device()
      check(bot.use_adb is True and bot.device_id == "127.0.0.1:5555",
            "an empty saved value keeps the default address, so only the flag can carry the news")
      with fake_adb() as adb:
        with captured_output() as said:
          ok = adb_actions.init_adb()
        check(ok is True and adb.connected == ["127.0.0.1:5555"],
              "the run still connects on the default address ...")
        check(any("127.0.0.1:5555" in msg for _, msg in said.records),
              "... and it says the address was a fallback, not a value read from the config")

    with Sandbox() as box:
      box.write_config("127.0.0.1:5565")
      core_config.reload_config()
      main_module.resolve_device()
      with fake_adb() as adb:
        with captured_output() as said:
          adb_actions.init_adb()
        check(adb.connected == ["127.0.0.1:5565"] and said.records == [],
              "and a Device ID that was actually saved is not warned about")
  finally:
    (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_test_on_another_address_with_the_saved_id_empty_saves_it():
  print("\nA test on another address, with the saved Device ID empty")
  tested = "127.0.0.1:5565"
  with Sandbox() as box:
    box.write_config("")
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": tested})))
        detail = result["detail"]
        check(result["status"] == "success", f"the test succeeds: {detail}")
        check(f"Saved: the bot will now run on '{tested}'." in detail,
              "and a green test on another address saves it: the bot will run on it from now")
        check("127.0.0.1:5555" not in detail,
              "without the old note naming the default: the tested address is now the one it runs on")
        check("on ''" not in detail, "and it does not claim the bot runs on nothing")
      check(box.read_config()["device_id"] == tested,
            "the empty saved value is replaced by the address the test verified")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot

  # The other half of the same state: no device_id key in the file at all, the
  # template layer underneath supplying the default. The test saves the same way.
  print("\nA test on another address, with no device_id key in the file")
  with Sandbox() as box:
    box.write_config(None)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": tested})))
        detail = result["detail"]
        check(result["status"] == "success", f"the test succeeds: {detail}")
        check(f"Saved: the bot will now run on '{tested}'." in detail,
              "and it saves the tested address even though the file only carried the template's default")
        check("127.0.0.1:5555" not in detail,
              "without the old note naming the default the empty value fell back to")
        check("on ''" not in detail, "and it does not claim the bot runs on nothing")
      check(box.read_config()["device_id"] == tested,
            "the file now carries the address explicitly, instead of the template standing in")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_test_save_writes_exactly_what_a_setup_save_would():
  print("\nA green test saves the address the way a Setup save would")
  tested = "127.0.0.1:5575"
  with Sandbox() as box:
    # A config that carries other setup keys: the test's save must touch only device_id.
    own = template()
    own["device_id"] = "127.0.0.1:5555"
    own["window_name"] = "Mumu 12"
    own["sleep_time_multiplier"] = 3
    write(box.config, own)
    write(server.GLOBAL_SETUP_PATH, {"use_adb": True, "device_id": "127.0.0.1:5555",
                                     "window_name": "Mumu 12"})
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        asyncio.run(server.test_adb(Request({"device_id": tested})))
      after = box.read_config()
      check(after["device_id"] == tested,
            "the tested address lands in the config the process runs on ...")
      check(after["window_name"] == "Mumu 12" and after["sleep_time_multiplier"] == 3,
            "and nothing else that config carries is touched by the test's save")
      shared = box.shared()
      check(shared.get("device_id") == tested, "... and in the shared file the same one key changes ...")
      check(shared.get("window_name") == "Mumu 12" and shared.get("use_adb") is True,
            "... without the file gaining or losing any other key")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot

  # A named instance proves nothing about the default instance's emulator.
  print("\nA named instance's test writes its own file alone")
  with Sandbox() as box:
    bot.instance_name = "left"
    instance = os.path.join(core_config.INSTANCE_DIR, "left.json")
    own = template()
    own["device_id"] = "127.0.0.1:5555"
    own["window_name"] = "Left Window"
    write(instance, own)
    shared_doc = {"use_adb": True, "device_id": "127.0.0.1:5599", "window_name": "Mumu 12"}
    write(server.GLOBAL_SETUP_PATH, shared_doc)
    shared_mtime = os.path.getmtime(server.GLOBAL_SETUP_PATH)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": "127.0.0.1:5565"})))
        check(result["status"] == "success", f"the test succeeds: {result['detail']}")
      after = json.loads(io.open(instance, encoding="utf-8").read())
      check(after["device_id"] == "127.0.0.1:5565",
            "the tested address lands in the instance's own file ...")
      check(after["window_name"] == "Left Window", "and nothing else in it is touched")
      check(os.path.getmtime(server.GLOBAL_SETUP_PATH) == shared_mtime,
            "and the shared file is not written by a named instance's test ...")
      check(box.shared() == shared_doc, "... not even in content: it stays the default instance's")
      check(bot.device_id == "127.0.0.1:5565",
            "and the instance process keeps the device it just proved")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_test_does_not_reset_a_shared_file_it_cannot_read():
  print("\nA green test, with a shared setup.json that cannot be read")
  tested = "127.0.0.1:5565"
  with Sandbox() as box:
    box.write_config("127.0.0.1:5555")
    # What a torn write leaves behind: JSON that stops mid-value, so the reader
    # returns None -- and the keys the file still carries on disk are the ones a
    # careless save would destroy.
    corrupt = '{"device_id": "127.0.0.1:5555", "use_adb": true, "window_name": "Mumu'
    io.open(server.GLOBAL_SETUP_PATH, "w", encoding="utf-8").write(corrupt)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      said = io.StringIO()
      with contextlib.redirect_stdout(said), fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": tested})))
      check(result["status"] == "success", f"the test itself still succeeds: {result['detail']}")
      check(box.read_config()["device_id"] == tested,
            "the tested address still lands in the config the process runs on ...")
      with io.open(server.GLOBAL_SETUP_PATH, encoding="utf-8") as handle:
        still = handle.read()
      check(still == corrupt,
            "... but the shared file it cannot parse is left exactly as it was: a test "
            "does not reset it to a device_id alone")
      check(any("[CONFIG]" in line and "setup.json" in line and "could not be read" in line
                for line in said.getvalue().splitlines()),
            "and the save says in [CONFIG] why the shared file was left as it is")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot

  # The same corruption under a repeat test of the saved address. That path promises
  # to write nothing anywhere, and a shared file it cannot read must not change that.
  print("\nA repeat test of the saved address, with the shared file unreadable")
  addr = "127.0.0.1:5565"
  with Sandbox() as box:
    box.write_config(addr)
    corrupt = '{"device_id": "' + addr + '", "window_name": "Mumu'
    io.open(server.GLOBAL_SETUP_PATH, "w", encoding="utf-8").write(corrupt)
    shared_mtime = os.path.getmtime(server.GLOBAL_SETUP_PATH)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": addr})))
        check(result["status"] == "success" and "The bot already runs on" in result["detail"],
              f"the repeat test still reports already: {result['detail']}")
      with io.open(server.GLOBAL_SETUP_PATH, encoding="utf-8") as handle:
        still = handle.read()
      check(still == corrupt and os.path.getmtime(server.GLOBAL_SETUP_PATH) == shared_mtime,
            "the unreadable shared file stays byte and mtime untouched")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_test_cannot_save_a_config_it_cannot_read():
  """A green test whose own-config write is guarded off reports only the truth.

  The guard that leaves an unreadable config untouched used to leave the response
  behind too: the route still answered "Saved: the bot will now run on '<address>'"
  while nothing anywhere (a named instance) or only setup.json, which the bot never
  reads (the default instance), carried the address -- and the failed reload that
  followed was then quoted as "ADB is currently off in the config", a claim about a
  file nobody just read.
  """
  tested = "127.0.0.1:5565"

  # A named instance writes its own file alone, so an unreadable one means the test
  # persisted nothing anywhere. The response must not say Saved, and the process must
  # go back to the device it came with.
  print("\nA green test, with the instance config it writes unreadable")
  with Sandbox() as box:
    bot.instance_name = "left"
    instance = os.path.join(core_config.INSTANCE_DIR, "left.json")
    corrupt = '{"device_id": "127.0.0.1:5555", "use_adb": true, "window_name": "Mumu'
    io.open(instance, "w", encoding="utf-8").write(corrupt)
    write(server.GLOBAL_SETUP_PATH, {"use_adb": True, "device_id": "127.0.0.1:5599"})
    own_mtime = os.path.getmtime(instance)
    shared_mtime = os.path.getmtime(server.GLOBAL_SETUP_PATH)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    # Whatever the process last believed about the switch, a reload that cannot read
    # the config says nothing about it -- seed the stale value the bug quoted.
    core_config.USE_ADB = False
    try:
      said = io.StringIO()
      with contextlib.redirect_stdout(said), fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": tested})))
      detail = result["detail"]
      check(result["status"] == "success", f"the test itself still succeeds: {detail}")
      check("Saved" not in detail,
            "it does not claim Saved: nothing was written anywhere")
      check(instance in detail and "could not be read" in detail,
            "and it names the unreadable config as the reason")
      check(any("[CONFIG]" in line and "left.json" in line and "could not be read" in line
                for line in said.getvalue().splitlines()),
            "and the save says in [CONFIG] why the instance file was left as it is")
      check("ADB is currently off" not in detail,
            "and no claim about the config's ADB switch follows a reload that "
            "could not read the config either")
      with io.open(instance, encoding="utf-8") as handle:
        still = handle.read()
      check(still == corrupt and os.path.getmtime(instance) == own_mtime,
            "the unreadable instance file stays byte and mtime untouched")
      check(os.path.getmtime(server.GLOBAL_SETUP_PATH) == shared_mtime,
            "and the shared file is untouched: a named instance writes no shared file")
      check(bot.device_id == "" and bot.use_adb is False,
            "and the process is restored to where the test found it: nothing was "
            "persisted, so driving the tested address would be a device nothing "
            "on disk names")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot

  # The default instance: the guarded own write leaves only setup.json carrying the
  # address -- the page's mirror, which load_config never reads. The report says
  # exactly that instead of "Saved: the bot will now run on ...".
  print("\nA green test, with the config the process runs on unreadable")
  with Sandbox() as box:
    corrupt = '{"device_id": "127.0.0.1:5555", "use_adb": false, "window_name": "Mumu'
    io.open(box.config, "w", encoding="utf-8").write(corrupt)
    own_mtime = os.path.getmtime(box.config)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    core_config.USE_ADB = False
    try:
      said = io.StringIO()
      with contextlib.redirect_stdout(said), fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": tested})))
      detail = result["detail"]
      check(result["status"] == "success", f"the test itself still succeeds: {detail}")
      check("Saved: the bot will now run on" not in detail,
            "it does not claim the bot will run on the tested address")
      check("Only setup.json was written" in detail and "could not be read" in detail,
            "and it says only setup.json was written and names the unreadable config")
      check(box.config in detail,
            "naming the file the bot actually runs on, not just any file")
      check("ADB is currently off" not in detail,
            "and no claim about the config's ADB switch follows a reload that "
            "could not read the config either")
      with io.open(box.config, encoding="utf-8") as handle:
        still = handle.read()
      check(still == corrupt and os.path.getmtime(box.config) == own_mtime,
            "the unreadable config stays byte and mtime untouched")
      check(box.shared().get("device_id") == tested,
            "while the shared file did take the address: it is the page's mirror, "
            "and it was readable")
      check(bot.device_id == "" and bot.use_adb is False,
            "and the process is restored: setup.json is not something the bot reads")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot

  # Both files unreadable: nothing written anywhere, and the report may not claim
  # Saved under any reading of "already".
  print("\nA green test, with both files it would write unreadable")
  with Sandbox() as box:
    corrupt = '{"device_id": "127.0.0.1:5555", "use_adb": false, "window_name": "Mumu'
    io.open(box.config, "w", encoding="utf-8").write(corrupt)
    shared_corrupt = '{"device_id": "127.0.0.1:5599", "window_name": "Mumu'
    io.open(server.GLOBAL_SETUP_PATH, "w", encoding="utf-8").write(shared_corrupt)
    own_mtime = os.path.getmtime(box.config)
    shared_mtime = os.path.getmtime(server.GLOBAL_SETUP_PATH)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with contextlib.redirect_stdout(io.StringIO()), fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": tested})))
      detail = result["detail"]
      check(result["status"] == "success", f"the test itself still succeeds: {detail}")
      check("Saved" not in detail,
            "it does not claim Saved when both writes were guarded off")
      check("Nothing was saved" in detail and "could not be read" in detail,
            "and it says nothing was saved, naming the unreadable config")
      with io.open(box.config, encoding="utf-8") as handle:
        still = handle.read()
      with io.open(server.GLOBAL_SETUP_PATH, encoding="utf-8") as handle:
        still_shared = handle.read()
      check(still == corrupt and os.path.getmtime(box.config) == own_mtime,
            "the unreadable config stays byte and mtime untouched")
      check(still_shared == shared_corrupt and
            os.path.getmtime(server.GLOBAL_SETUP_PATH) == shared_mtime,
            "as does the unreadable shared file")
      check(bot.device_id == "" and bot.use_adb is False,
            "and the process is restored to where the test found it")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot

  # The --use-adb branch of the same guard: the pin claim stays, but the save claim
  # tells the truth about a config nothing could be written to.
  print("\nA --use-adb process, with the config it saves to unreadable")
  cli = "127.0.0.1:5555"
  saved_args = main_module.args.use_adb
  try:
    with Sandbox() as box:
      corrupt = '{"device_id": "127.0.0.1:5555", "use_adb": true, "window_name": "Mumu'
      io.open(box.config, "w", encoding="utf-8").write(corrupt)
      main_module.args.use_adb = cli
      bot.use_adb, bot.device_id, bot.device_id_is_default = True, cli, False
      bot.is_bot_running = False
      saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
      try:
        with fake_adb() as adb:
          result = asyncio.run(server.test_adb(Request({"device_id": tested})))
          detail = result["detail"]
          check(result["status"] == "success", f"the test itself still succeeds: {detail}")
          check("Saved" not in detail and "could not be read" in detail,
                "it reports the unreadable config instead of a save that did not happen")
          check(f"keeps driving '{cli}'" in detail,
                "and it still names the command-line address as the one this process drives")
        check(bot.device_id == cli,
              "and the process stays on the command-line address")
      finally:
        (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot
  finally:
    main_module.args.use_adb = saved_args


def a_green_test_with_adb_off_in_the_config_says_the_window_stays():
  print("\nA green test, with ADB off in the saved config")
  tested = "127.0.0.1:5565"
  with Sandbox() as box:
    box.write_config("127.0.0.1:5555", use_adb=False)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        detail = asyncio.run(server.test_adb(Request({"device_id": tested})))["detail"]
        check(f"Saved: the bot will now run on '{tested}'." in detail,
              "the address is saved even though the switch is off ...")
        check("ADB is currently off in the config" in detail
              and "keep using the window" in detail,
              "... and it says the bot stays on the window until the switch is turned on")
      check(box.read_config()["use_adb"] is False,
            "and saving the address does not flip the switch: it stays off")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot

  with Sandbox() as box:
    box.write_config(tested, use_adb=False)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb() as adb:
        detail = asyncio.run(server.test_adb(Request({"device_id": tested})))["detail"]
        check(f"The bot already runs on '{tested}'." in detail and "Saved:" not in detail,
              "and with the address already saved and the switch off, it says so ...")
        check("ADB is currently off in the config" in detail,
              "with the same note: the bot will keep using the window")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_test_that_cannot_connect_says_so_and_saves_nothing():
  print("\nA test that cannot connect says so and saves nothing")
  saved = "127.0.0.1:5565"
  unreachable = "127.0.0.1:5575"
  with Sandbox() as box:
    box.write_config(saved)
    own_mtime = os.path.getmtime(box.config)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, False
    try:
      with fake_adb(unreachable={unreachable}) as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": unreachable})))
        check(result["status"] == "fail", f"the test fails: {result['detail']}")
        check(result["detail"] == f"Could not connect to '{unreachable}'. "
              "Ensure the emulator is running and ADB is enabled.",
              "with exactly the message it always gave")
      check(box.read_config()["device_id"] == saved and
            os.path.getmtime(box.config) == own_mtime,
            "and it saved nothing, on disk as well as in the process")
      check(bot.use_adb is False and bot.device_id == "",
            "and the process is restored to where the test found it")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_running_bot_is_not_tested_over():
  print("\nA test over a running bot is refused")
  with Sandbox() as box:
    box.write_config("127.0.0.1:5565")
    own_mtime = os.path.getmtime(box.config)
    saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
    bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running = False, "", False, True
    try:
      result = asyncio.run(server.test_adb(Request({"device_id": "127.0.0.1:5575"})))
      check(result["status"] == "fail" and result["detail"].startswith("The bot is running."),
            f"the test is refused while the bot runs: {result['detail']}")
      check(box.read_config()["device_id"] == "127.0.0.1:5565"
            and os.path.getmtime(box.config) == own_mtime,
            "and it touches nothing: a live session's device is not re-pointed mid-run")
    finally:
      (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_start_that_cannot_reach_the_device_says_why():
  print("\nA start that cannot reach the device, in ADB mode")
  saved_bot = (bot.use_adb, bot.device_id, bot.is_bot_running)
  saved_claim = main_module.claim_device
  try:
    # One bot per device is covered by its own suite; here the claim is not the subject.
    main_module.claim_device = lambda: None

    with Sandbox() as box:
      saved_addr = "127.0.0.1:5565"
      box.write_config(saved_addr)
      bot.is_bot_running = True
      with fake_adb(unreachable={saved_addr}) as adb:
        with captured_output() as said:
          main_module._run_bot()
        failed = [msg for level, msg in said.records if level >= logging.ERROR]
        check(any(f"'{saved_addr}'" in msg and "saved in config" in msg for msg in failed),
              "the failure names the device it tried and says the bot drives the saved one")
        check(any("Test button" in msg and "saves a verified address" in msg for msg in failed),
              "and that the Test button saves a verified address itself, so a tested "
              "address needs no page saving")
        check(not any("save the page" in msg for msg in failed),
              "and nothing on this path still says to save the page: a red test must "
              "not be saved, a green one saves itself")
        check(not any("Failed to focus Umamusume window" in msg for _, msg in said.records),
              "it does not report a window focus failure that never happened in ADB mode")
        check(bot.is_bot_running is False, "and the run is reported stopped, not left running")

    with Sandbox() as box:
      box.write_config("")
      bot.device_id = ""
      bot.is_bot_running = True
      with fake_adb(unreachable={"127.0.0.1:5555"}) as adb:
        with captured_output() as said:
          main_module._run_bot()
        failed = [msg for level, msg in said.records if level >= logging.ERROR]
        check(any("'127.0.0.1:5555'" in msg for msg in failed),
              "with an empty Device ID, the failure names the default address the bot actually fell back to")
  finally:
    main_module.claim_device = saved_claim
    bot.use_adb, bot.device_id, bot.is_bot_running = saved_bot


def a_cli_pinned_process_says_what_it_drives():
  print("\nA process pinned by --use-adb: the flag's address is the one it drives")
  cli = "127.0.0.1:5555"
  tested = "127.0.0.1:5565"
  saved_args = main_module.args.use_adb
  saved_control = dict(server.BOT_CONTROL)
  # Production wires resolve_device into the server at startup (register_bot_control in
  # main.py), and a green test's _apply_saved_config calls it back -- whose CLI branch is
  # what pins the process to the flag's address. Without the same wiring here, the suite
  # would be testing a server that cannot happen.
  server.register_bot_control(resolve_device=main_module.resolve_device)
  saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
  try:
    with Sandbox() as box:
      # The saved config carries a third address, so every "which device" claim has a
      # distinct value to be true about: the flag's, the tested one, the saved one.
      box.write_config("127.0.0.1:5599")
      main_module.args.use_adb = cli
      bot.use_adb, bot.device_id, bot.device_id_is_default = True, cli, False
      bot.is_bot_running = False
      with fake_adb() as adb:
        result = asyncio.run(server.test_adb(Request({"device_id": tested})))
        detail = result["detail"]
        check(result["status"] == "success", f"the test succeeds: {detail}")
        check("Saved" in detail and "config" in detail,
              "the tested address is still saved, and the report says to where ...")
        check("will now run on" not in detail,
              "without the unreserved claim that the bot will now run on the tested address ...")
        check(f"keeps driving '{cli}'" in detail,
              "and it names the command-line address as the one this process keeps driving")
      check(box.read_config()["device_id"] == tested,
            "the save itself is unchanged: the tested address lands in the config")
      check(bot.device_id == cli,
            "and the process is on the command-line address: resolve_device pinned it back inside the test itself")

    # The not-fully-green half in the same mode: connected, no frame. The report must
    # name the address this process actually drives, not the saved config's.
    with Sandbox() as box:
      box.write_config("127.0.0.1:5599")
      main_module.args.use_adb = cli
      bot.use_adb, bot.device_id, bot.device_id_is_default = True, cli, False
      bot.is_bot_running = False
      with fake_adb() as adb:
        real_screenshot = adb_actions.screenshot

        def broken_screenshot(*args, **kwargs):
          raise RuntimeError("no frame")

        adb_actions.screenshot = broken_screenshot
        try:
          result = asyncio.run(server.test_adb(Request({"device_id": tested})))
        finally:
          adb_actions.screenshot = real_screenshot
        detail = result["detail"]
        # Reported, and reported as a failure: this used to expect "success", which the page
        # draws green, for a device the bot cannot read (review of 8f5ae63, 2026-09-23).
        check(result["status"] == "fail" and detail.startswith(f"Connected to '{tested}'"),
              f"a device that connects but cannot be screenshotted is reported, as a "
              f"failure: {result['status']}: {detail}")
        check(f"this process drives '{cli}'" in detail,
              "and the report names the command-line address as the one the process runs on ...")
        check("Save the page" not in detail,
              "without advising a page save: no save can re-point a --use-adb process")
      check(bot.device_id == cli,
            "and the failed test left the process on the command-line address")

    # A start that cannot reach the flag's address: the report is about the pin, not
    # about a saved config nothing in this process reads.
    with Sandbox() as box:
      box.write_config("127.0.0.1:5599")
      main_module.args.use_adb = cli
      bot.is_bot_running = True
      saved_claim = main_module.claim_device
      # One bot per device is covered by its own suite; here the claim is not the subject.
      main_module.claim_device = lambda: None
      try:
        with fake_adb(unreachable={cli}) as adb:
          with captured_output() as said:
            main_module._run_bot()
          failed = [msg for level, msg in said.records if level >= logging.ERROR]
          check(any(f"--use-adb '{cli}'" in msg and "cannot re-point" in msg for msg in failed),
                "the failure names the command-line pin and says the saved config cannot re-point it")
          check(any("launches without --use-adb" in msg and "Test button" in msg for msg in failed),
                "and that a green Test saves to the config, for launches without the flag")
          check(not any("drives the Device ID saved in config." in msg for msg in failed),
                "without the unqualified claim that the bot drives the saved config's device")
          check(bot.is_bot_running is False, "and the run is reported stopped, as before")
      finally:
        main_module.claim_device = saved_claim

    # Testing the flag's own address is no divergence: the process drives exactly what
    # was tested, so the plain claim stays true there.
    with Sandbox() as box:
      box.write_config("127.0.0.1:5599")
      main_module.args.use_adb = cli
      bot.use_adb, bot.device_id, bot.device_id_is_default = True, cli, False
      bot.is_bot_running = False
      with fake_adb() as adb:
        detail = asyncio.run(server.test_adb(Request({"device_id": cli})))["detail"]
        check(f"will now run on '{cli}'" in detail,
              "a test of the flag's own address keeps the plain claim: the process runs on exactly what it tested")
  finally:
    main_module.args.use_adb = saved_args
    server.BOT_CONTROL.clear()
    server.BOT_CONTROL.update(saved_control)
    (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def the_test_and_the_start_cannot_interleave():
  """A start cannot land in the window between the test's check and its re-point.

  Both test_adb and start_bot read is_bot_running once and then act on it, so a start
  that slipped in between the test's check and its device re-point began a run on
  fields the test was overwriting: a live session re-pointed mid-run and, a green test
  persisting its address since 1.0.4, the tested address written to the config and the
  shared file with the finally's restore skipped. The window is microseconds and
  cannot be reproduced offline, so the lock both paths now hold is asserted in the
  source, and the wait each does is exercised by holding it by hand -- which holds it
  for as long as the window would be, deterministically.
  """
  print("\nThe lock the test and the start share:")
  def tree_of(path):
    return ast.parse(io.open(path, encoding="utf-8").read())

  def function_def(tree, name):
    for node in ast.walk(tree):
      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
        return node
    raise AssertionError(f"no function {name}")

  def is_flag_check(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Attribute)
            and node.test.attr == "is_bot_running"
            and isinstance(node.test.value, ast.Name) and node.test.value.id == "bot")

  def is_flag_raise(node):
    return (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            and node.value.value is True
            and any(isinstance(t, ast.Attribute) and t.attr == "is_bot_running"
                    and isinstance(t.value, ast.Name) and t.value.id == "bot"
                    for t in node.targets))

  def is_lock_with(node):
    return (isinstance(node, ast.With) and len(node.items) == 1
            and isinstance(node.items[0].context_expr, ast.Attribute)
            and node.items[0].context_expr.attr == "bot_state_lock"
            and isinstance(node.items[0].context_expr.value, ast.Name)
            and node.items[0].context_expr.value.id == "bot")

  def contains(outer, inner):
    return outer.lineno <= inner.lineno and inner.end_lineno <= outer.end_lineno

  locks = [node for node in ast.walk(tree_of("core/bot.py"))
           if isinstance(node, ast.Assign)
           and any(isinstance(t, ast.Name) and t.id == "bot_state_lock"
                   for t in node.targets)]
  check(len(locks) == 1 and isinstance(locks[0].value, ast.Call)
        and isinstance(locks[0].value.func, ast.Attribute)
        and locks[0].value.func.attr == "Lock",
        "core/bot.py defines one bot_state_lock, a threading.Lock()")

  test_adb = function_def(tree_of("server/main.py"), "test_adb")
  locked = [node for node in test_adb.body if is_lock_with(node)]
  check(len(locked) == 1, "test_adb acquires the lock at one place")
  if locked:
    with_node = locked[0]
    guarded = [node for node in ast.walk(with_node) if is_flag_check(node)]
    check(len(guarded) == 1 and contains(with_node, guarded[0]),
          "its flag check lives inside the with, not before it")
    check(any(isinstance(node, ast.Try) and contains(with_node, node)
              for node in ast.walk(with_node)),
          "and so does the try/finally that restores or keeps the save")

  main_tree = tree_of("main.py")
  start_bot = function_def(main_tree, "start_bot")
  locked_starts = [node for node in start_bot.body if is_lock_with(node)]
  check(len(locked_starts) == 1, "start_bot acquires the same lock at one place")
  if locked_starts:
    with_node = locked_starts[0]
    guarded = [node for node in ast.walk(with_node) if is_flag_check(node)]
    check(len(guarded) == 1 and contains(with_node, guarded[0]),
          "its flag check is inside the with")
    raised = [node for node in ast.walk(with_node) if is_flag_raise(node)]
    check(len(raised) == 1 and contains(with_node, raised[0]),
          "and so is the flag it raises: the whole claim is one critical section")
    check([node for node in ast.walk(main_tree) if is_flag_raise(node)] == raised,
          "no other code in main.py raises the flag outside start_bot's lock")
  check(not [node for node in ast.walk(tree_of("server/main.py")) if is_flag_raise(node)],
        "and the server module never raises the flag itself")

  # Then the wait itself, with the lock held by hand for as long as the window would
  # be. The spawn is stubbed: a released start must not begin a real bot in the suite.
  print("\nEach side waits for the other:")
  saved_main = main_module.main
  saved_bot = (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running)
  try:
    main_module.main = lambda: None
    bot.is_bot_running = False
    with bot.bot_state_lock:
      outcomes = []
      starter = threading.Thread(target=lambda: outcomes.append(main_module.start_bot()))
      starter.start()
      starter.join(1.0)
      check(starter.is_alive(),
            "a start arriving during the test's sequence waits for the lock")
      check(bot.is_bot_running is False,
            "and it has raised no flag while it waits")
    starter.join(5.0)
    check(outcomes == [True] and bot.is_bot_running is True,
          "released, it starts: first to the lock, first to run")

    bot.is_bot_running = False
    with Sandbox() as box:
      box.write_config("127.0.0.1:5565")
      bot.use_adb, bot.device_id, bot.device_id_is_default = False, "", False
      with fake_adb(unreachable={"127.0.0.1:5575"}) as adb:
        results = []
        tester = threading.Thread(target=lambda: results.append(asyncio.run(
            server.test_adb(Request({"device_id": "127.0.0.1:5575"})))))
        with bot.bot_state_lock:
          tester.start()
          tester.join(1.0)
          check(tester.is_alive(),
                "a test arriving during a start's critical section waits for the lock")
          check(bot.device_id == "" and bot.use_adb is False,
                "and it has re-pointed nothing while it waits")
        tester.join(5.0)
        result = results[0]
        check(result["status"] == "fail" and "Could not connect" in result["detail"],
              "released, the test runs -- and this one connects to nothing")
        check(box.read_config()["device_id"] == "127.0.0.1:5565",
              "so the config a red test was parked against is untouched")
        check(bot.use_adb is False and bot.device_id == "",
              "and the finally restored the fields the test had held")
  finally:
    main_module.main = saved_main
    (bot.use_adb, bot.device_id, bot.device_id_is_default, bot.is_bot_running) = saved_bot


def a_use_button_tests_the_serial_it_points_at():
  print("\nThe page")
  # Read out of the source rather than exercised, because the failure is in a React
  # handler and nothing in Python can reach it. The built bundle is read too --
  # web/dist is what the server actually serves, so a fix left unbuilt is a fix
  # that does not exist.
  with io.open("web/src/components/set-up/SetUpSection.tsx", encoding="utf-8") as handle:
    component = handle.read()
  # The Test button's own fetch sends the Device ID state; the Use button's must send
  # the serial, because that state only picks the new value up on the next render, and
  # a test on the old address would be a lie about which emulator the bot will run on.
  at = component.find("const selectDevice")
  body = component[at:at + 500] if at >= 0 else ""
  check('"/adb/test"' in body and "device_id: serial" in body,
        "the Use button tests the serial it points at, not the not-yet-re-rendered Device ID state")
  check("adbTestResult.detail" in component,
        "and the test's report -- including which device the bot runs on -- is the line the page shows")
  with io.open("web/dist/app.js", encoding="utf-8") as handle:
    check("Look on common emulator ports" in handle.read(),
          "and the built bundle carries it -- web/dist is what the server serves")


def main():
  a_green_test_on_another_address_saves_it()
  a_test_on_the_saved_address_writes_nothing()
  an_empty_device_id_says_it_fell_back()
  a_run_resolved_from_an_empty_config_says_it_is_on_the_default()
  a_test_on_another_address_with_the_saved_id_empty_saves_it()
  a_test_save_writes_exactly_what_a_setup_save_would()
  a_test_does_not_reset_a_shared_file_it_cannot_read()
  a_test_cannot_save_a_config_it_cannot_read()
  a_green_test_with_adb_off_in_the_config_says_the_window_stays()
  a_test_that_cannot_connect_says_so_and_saves_nothing()
  a_running_bot_is_not_tested_over()
  the_test_and_the_start_cannot_interleave()
  a_start_that_cannot_reach_the_device_says_why()
  a_cli_pinned_process_says_what_it_drives()
  a_use_button_tests_the_serial_it_points_at()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("What the ADB test and a failed start say is the device the bot is on.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
