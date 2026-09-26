import sys
import os
import subprocess
import warnings
warnings.filterwarnings(
  "ignore",
  category=UserWarning,
  module=r"torch\.utils\.data\.dataloader"
)
MIN = (3, 11)
MAX = (3, 14)

if not (MIN <= sys.version_info < MAX):
  # ask the launcher what it has
  out = subprocess.check_output(
    ["py", "--list"],
    text=True,
    stderr=subprocess.DEVNULL
  )

  candidates = []
  for line in out.splitlines():
    line = line.strip()
    if line.startswith("-V:"):
      v = line.split()[0][3:]
      try:
        major, minor = map(int, v.split("."))
        if (major, minor) >= MIN and (major, minor) < MAX:
          candidates.append(v)
      except ValueError:
        pass

  if not candidates:
    raise RuntimeError("No compatible Python 3.11-3.13 installed")

  best = sorted(candidates)[-1]

  p = subprocess.Popen(
    ["py", f"-{best}", *sys.argv],
    stdin=sys.stdin,
    stdout=sys.stdout,
    stderr=sys.stderr
  )
  p.wait()
  sys.exit(p.returncode)

from utils.tools import sleep
import pygetwindow as gw
import threading
import uvicorn
import keyboard

import time
import sys
import socket
import traceback

import utils.constants as constants
from utils.log import info, error, debug, warning, args, init_logging
from utils.device_action_wrapper import BotStopException

from scenarios.independent_training import independent_training_loop
import core.config as config
from core.device_claim import claim as claim_device
import core.bot as bot
from server.main import app
import server.main as server_main
from update_config import update_config
from utils.notifications import on_started

bot.windows_window = None

# The Steam client's window title, matched without regard to case.
STEAM_WINDOW_TITLE = "Umamusume"

def focus_umamusume():
  if bot.use_adb:
    info("Using ADB - no need to focus window.")
    from utils.adb_actions import init_adb
    if not init_adb():
      error("Could not connect to ADB device. Please verify your device ID / port in config.")
      return False
    constants.adjust_constants_x_coords(offset=-155)
    return True
  try:
    import pyautogui
    from utils.pyautogui_actions import screen_to_world_conversion_init
    # Compared without case. getWindowsWithTitle already ignores it, but this test did
    # not, so a client that titles its window "umamusume" instead of "Umamusume" was
    # found and then thrown away -- which is exactly what happened when the game changed
    # the casing, and would happen again in reverse if it changed back.
    win = gw.getWindowsWithTitle(STEAM_WINDOW_TITLE)
    target_window = next(
      (w for w in win if w.title.strip().casefold() == STEAM_WINDOW_TITLE.casefold()), None)
    if not target_window:
      info(f"Couldn't get the steam version window, trying {config.WINDOW_NAME}.")
      if not config.WINDOW_NAME:
        error("Window name cannot be empty! Please set window name in the config.")
        return False
      win = gw.getWindowsWithTitle(config.WINDOW_NAME)
      target_window = next(
        (w for w in win if w.title.strip().casefold() == config.WINDOW_NAME.casefold()),
        None)
      if not target_window:
        error(f"Couldn't find target window named \"{config.WINDOW_NAME}\". Please double check your window name config.")
        return False

      constants.adjust_constants_x_coords()
      if target_window.isMinimized:
        target_window.restore()
      else:
        target_window.minimize()
        sleep(0.2)
        target_window.restore()
        sleep(0.5)
      pyautogui.press("esc")
      pyautogui.press("f11")
      time.sleep(5)
      close_btn = pyautogui.locateCenterOnScreen("assets/buttons/bluestacks/close_btn.png", confidence=0.8, minSearchTime=2)
      if close_btn:
        pyautogui.click(close_btn)
      # Every desktop capture is taken of this window. It was only ever set on the Steam
      # path below, so this path found its window, went full screen, and then raised on
      # the first screenshot -- every run, for everyone on an emulator without ADB.
      bot.windows_window = target_window
      return True

    if target_window.width < 1920 or target_window.height < 1080:
      error(f"Your resolution is {target_window.width} x {target_window.height}. Minimum expected size is 1920 x 1080.")
      return False
    # The Steam window is the frame the constants are authored in. Asked for explicitly
    # rather than assumed: a process that ran over ADB or the window fallback first has
    # rebased them away from it, and the rebase is a no-op when they are already here.
    constants.adjust_constants_x_coords(offset=0)
    if target_window.isMinimized:
      target_window.restore()
    else:
      target_window.minimize()
      sleep(0.2)
      target_window.restore()
      sleep(0.5)
    bot.windows_window = target_window
    if target_window.width > 1920 or target_window.height > 1080:
      info("Screen bigger than standard 1080p. Initializing screen space conversions.")
      screen_to_world_conversion_init()
  except Exception as e:
    error(f"Error focusing window: {e}")
    return False
  return True

def resolve_device():
  """Which emulator this process drives, from --use-adb or the config.

  Called at startup as well as when the bot starts, because the server needs the answer
  before then: every state file is keyed on the device, and a process that has not
  decided yet reads `desktop`'s. The Overview showed an empty queue for itself and listed
  the real emulator as somebody else's instance, which is how this was noticed.
  """
  if args.use_adb:
    bot.use_adb = True
    bot.device_id = args.use_adb
    bot.device_id_is_default = False
  else:
    bot.use_adb = config.USE_ADB
    if config.DEVICE_ID and config.DEVICE_ID != "":
      bot.device_id = config.DEVICE_ID
      bot.device_id_is_default = False
    else:
      # Empty in the config: bot.device_id keeps the seed value from bot.py, and without
      # the flag nothing later can tell a fallback apart from a value the config gave.
      bot.device_id_is_default = True


def relaunch_instances(server, timeout=30.0):
  """Start again the named instances an update closed (core.restart's record).

  Waits for this server to be up first: a launch picks a free port by asking the others,
  and this instance's own port has to be answering to be counted as taken.
  """
  from core import restart
  names = restart.take_relaunch()
  if not names:
    return
  deadline = time.monotonic() + timeout
  while not server.started and time.monotonic() < deadline:
    time.sleep(0.2)
  for name in names:
    try:
      outcome = server_main.launch_instance(name)
      info(f"Restarted instance '{name}' on port {outcome['port']}.")
    except Exception as exception:                                 # noqa: BLE001
      detail = getattr(exception, "detail", exception)
      warning(f"Could not restart instance '{name}' after the update: {detail}")


def say_if_behind():
  """One line at startup when a newer build is published. Says; does not pull.

  Runs on its own thread and swallows everything: this is a courtesy, and a bot that
  fails to start because GitHub is having a bad morning would be a poor trade for it.
  """
  if not getattr(config, "AUTO_CHECK_UPDATES", True):
    return
  try:
    import core.updates as updates
    answer = updates.status()
    if answer.get("behind"):
      info(f"Version {answer['latest']} is out -- you are on {answer['current']}. "
           f"Update from the banner in the web UI, or download the latest zip over "
           f"this folder if it came from one. {answer['url']}")
  except Exception:
    pass


def main():
  """One bot run, on the start thread. Whatever ends it, its claims go with it."""
  try:
    _run_bot()
  finally:
    from core.device_claim import release_run
    release_run()


def _run_bot():
  print("Mirako Machine!")
  config.reload_config()
  bot.stop_after_career = False

  resolve_device()
  # Claimed before anything drives the screen. Two bots on one emulator do not share the
  # work -- they interleave taps, and each reads the other's navigation as a screen it did
  # not ask for.
  holder = claim_device()
  if holder:
    error(f"'{bot.device_id}' is already being driven by {holder}. Point this instance at "
          "its own emulator with --use-adb, or stop the other one.")
    bot.is_bot_running = False
    return

  if focus_umamusume():
    if bot.use_adb:
      from utils.adb_actions import emulator_identity
      from core.device_claim import claim_emulator
      identity = emulator_identity()
      holder = claim_emulator(identity)
      if holder:
        if identity:
          error(f"The emulator at '{bot.device_id}' is already being driven by {holder} -- "
                "the same emulator under another address. Point this instance at its own "
                "emulator, or stop the other one.")
        else:
          error(f"Could not read which emulator '{bot.device_id}' is, so another bot on it "
                "cannot be ruled out. Check the emulator is responding, then start again.")
        bot.is_bot_running = False
        return
    on_started()
    info(f"Config: {config.CONFIG_NAME}")
    debug(f"Config:")
    for name, value in vars(config).items():
      if name.startswith("__"):
        continue
      rendered = str(value)
      if len(rendered) > 300:
        rendered = f"{rendered[:300]}... ({len(rendered)} chars)"
      debug(f"{name} = {rendered}")

    info("Starting Independent Training.")
    try:
      independent_training_loop()
    except BotStopException:
      # An intentional stop -- the loop's own _stop() with a written reason, or F1
      # observed between handlers. Not a failure; logging it as one buried the real
      # message under a traceback that looked like a crash.
      info("Independent Training stopped.")
    except Exception:
      # main() is the body of a daemon thread, so an escaping exception is printed to
      # stderr by the threading hook and then the thread is gone -- while is_bot_running
      # is still True. The bot is dead and the flag says otherwise, so the next hotkey
      # press "stops" a corpse and only the one after it starts anything.
      #
      # Logged rather than reraised because the stderr traceback never reaches the log
      # file, which is what makes a crash like this hard to diagnose after the fact.
      error(f"Independent Training stopped on an unexpected error:\n"
            f"{traceback.format_exc()}")
      # A traceback says where the code was; a picture says what the game was showing,
      # which is usually the half that explains it. Reached with debug images off as
      # often as on, so it does not consult that flag.
      try:
        from utils.log import save_incident_image
        import utils.device_action_wrapper as device_action
        device_action.flush_screenshot_cache()
        save_incident_image(
          device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX), "crash")
      except Exception:  # noqa: BLE001 - the traceback above is the important part
        pass
    finally:
      bot.is_bot_running = False
  else:
    # Cleared here as well as in the `finally` above. Only the success path had it, so a
    # failure to start left the flag saying the bot was running: the next hotkey press
    # stopped a corpse and the one after it started anything. Every refusal added since
    # goes through here, which is what made the gap worth closing.
    if bot.use_adb:
      # In ADB mode focus_umamusume touches no window, so the window message would name
      # a failure that never happened. Name the device the bot actually tried instead.
      if args.use_adb:
        # A --use-adb process drives the flag's address; resolve_device pins it there
        # no matter what the saved config says, so the report is about the pin. The
        # Test button's save is real but governs launches without the flag.
        error(f"Could not connect to ADB device '{bot.device_id or "127.0.0.1:5555"}'. "
              f"This process was started with --use-adb '{args.use_adb}', so that is the "
              "address it drives -- the Device ID saved in config cannot re-point it. "
              "Make sure the emulator at that address is running and ADB is enabled, or "
              "restart without --use-adb to drive the saved Device ID. The Test button "
              "on the Setup page saves a verified address to the config for launches "
              "without --use-adb -- a green Test cannot move this process.")
      else:
        # The device the bot tried is the saved config's. The Test button saves a
        # verified address itself, so the advice is not "save the page" -- after a red
        # test that would pin the bot to an address it just proved unreachable -- but
        # to reach the saved device again, or to Use a live emulator from the Setup
        # page's ADB devices list.
        error(f"Could not connect to ADB device '{bot.device_id or "127.0.0.1:5555"}'. "
              "The bot drives the Device ID saved in config. The Test button on the Setup "
              "page saves a verified address itself -- a green Test makes that address "
              "the bot's device, and a red one never does -- so make sure the emulator "
              "at that address is running and ADB is enabled, or pick a live one from "
              "the ADB devices list there and Use it: that tests and saves it in one action.")
    else:
      error("Failed to focus Umamusume window")
    bot.is_bot_running = False

_last_stop_after_request = 0.0


def request_stop_after_career():
  """Arm or disarm "finish the career in progress, then stop".

  Whether a modified function key also satisfies a wait() on the bare key depends on the
  keyboard backend, so this is reachable by two routes for one press. Repeats within half
  a second are ignored rather than toggling straight back off.
  """
  global _last_stop_after_request
  now = time.monotonic()
  if now - _last_stop_after_request < 0.5:
    return
  _last_stop_after_request = now

  if not bot.is_bot_running:
    warning(f"Nothing is running -- press '{bot.hotkey}' to start.")
    return

  bot.stop_after_career = not bot.stop_after_career
  if bot.stop_after_career:
    info("Armed: stopping once the career in progress finishes. Press again to cancel.")
  else:
    info("Cancelled -- careers will keep running.")


def start_bot():
  """Start the run on its own thread. Safe to call twice -- the second is a no-op.

  Pulled out of the hotkey listener so the web UI can call the same thing. The hotkey is
  no longer the only way in, which it has to stop being: it fires whatever window has
  focus, f1..f10 runs out, and the instance it names comes from whichever port was free.

  The check and the set share bot.bot_state_lock with the ADB test, which holds it
  across its own flag check and the device re-point that follows: a start that set the
  flag inside the test's window began a run on device fields the test was overwriting,
  and a green test then persisted the tested address with the finally's restore
  skipped. Under the lock the two paths order completely -- whichever takes it first
  proceeds whole, the other sees the flag already true and refuses or waits.
  """
  with bot.bot_state_lock:
    if bot.is_bot_running:
      return False
    print("[BOT] Starting...")
    bot.is_bot_running = True
  # Spawned outside the critical section: the flag is already true, so any test that
  # acquires the lock next refuses, and the run's startup never waits on the lock.
  threading.Thread(target=main, daemon=True).start()
  return True


def stop_bot_now():
  """Ask the run to stop at its next check. The loop does the actual stopping."""
  if not bot.is_bot_running:
    return False
  print("[BOT] Stopping...")
  bot.is_bot_running = False
  return True


def hotkey_listener():
  try:
    keyboard.add_hotkey(f"shift+{bot.hotkey}", request_stop_after_career)
  except Exception as exception:
    # Losing the modified combination is survivable -- the check below still catches it
    # when the bare wait() sees shift held. Losing this thread is not: it is the only
    # thing listening for start/stop.
    warning(f"Could not register 'shift+{bot.hotkey}' ({exception}).")
  while True:
    keyboard.wait(bot.hotkey)
    # If the bare wait() was satisfied by the modified press, it is the finish-and-stop
    # request rather than a start/stop toggle.
    if keyboard.is_pressed("shift"):
      request_stop_after_career()
      sleep(0.5)
      continue
    if not start_bot():
      stop_bot_now()
    sleep(0.5)

def is_port_available(host, port):
  try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, port))
    sock.close()
    return True
  except OSError:
    return False

def start_server():
  host = "127.0.0.1"
  start_port = 8000
  end_port = 8010

  if args.port:
    port = args.port
    if not is_port_available(host, port):
      print(f"[ERROR] Port {port} is pinned with --port but already in use. "
            "Another instance is probably already on it.")
      raise SystemExit(1)
    # A pinned port inside the scanned range takes the same number and hotkey a scan
    # would have given it. It used to keep instance 1's, so an instance launched on :8003
    # answered F1 alongside the first one, and one key press started both bots.
    if start_port <= port < end_port:
      bot.instance = port - start_port + 1
      bot.hotkey = f"f{bot.instance}"
  else:
    # The positional fallback, unchanged in what it assigns. What is new is that running
    # out of ports is now a refusal: the loop used to fall through with `port` left on
    # the last candidate and instance/hotkey still at their defaults, so an eleventh
    # instance bound a port in use and wrote its logs over the first one.
    port = None
    for candidate in range(start_port, end_port):
      if is_port_available(host, candidate):
        port = candidate
        bot.instance = candidate - start_port + 1
        bot.hotkey = f"f{bot.instance}"
        break
      print(f"[INFO] Port {candidate} is already in use. Trying {candidate + 1}...")
    if port is None:
      print(f"[ERROR] No free port between {start_port} and {end_port - 1}. "
            "Stop an instance, or pin one with --port.")
      raise SystemExit(1)

  if args.hotkey:
    bot.hotkey = args.hotkey

  # The web UI drives the same two functions the hotkey does.
  # `tasks` is what lets the Overview list a task that has never been deferred -- the
  # schedule file only ever holds the ones that have waited for something -- and say
  # which ones are switched off. Read fresh on every request, so a setting changed in
  # the UI shows up without restarting anything.
  from scenarios.independent_training import build_tasks
  server_main.register_bot_control(
      start=start_bot, stop=stop_bot_now,
      stop_after_career=request_stop_after_career,
      # So a device changed in Setup reaches bot.device_id without waiting for the next
      # run -- everything keyed on the device is otherwise stale until then.
      resolve_device=resolve_device,
      tasks=lambda: [{"name": t.name, "enabled": bool(t.enabled())}
                     for t in build_tasks()])
  threading.Thread(target=hotkey_listener, daemon=True).start()
  bot.port = port
  server_config = uvicorn.Config(app, host=host, port=port, workers=1, log_level="warning")
  server = uvicorn.Server(server_config)
  init_logging()
  threading.Thread(target=say_if_behind, daemon=True).start()
  if not bot.instance_name:
    threading.Thread(target=relaunch_instances, args=(server,), daemon=True).start()
  info(f"Instance '{bot.instance_label()}' on port {port}.")
  info(f"Press '{bot.hotkey}' to start/stop the bot.")
  info(f"Press 'shift+{bot.hotkey}' to stop once the career in progress finishes.")
  info(f"[SERVER] Open http://{host}:{port} to configure the bot.")
  server.run()

if __name__ == "__main__":
  # Declared here rather than in start_server, which runs after both calls below. Which
  # file this process reads depends on which instance it is, so the name has to be set
  # before the first thing that reads one.
  if args.instance:
    bot.instance_name = args.instance
    os.makedirs(config.INSTANCE_DIR, exist_ok=True)
    from core.device_claim import claim_instance
    if not claim_instance(args.instance):
      print(f"[ERROR] Instance '{args.instance}' is already running. Switch to its tab in "
            "the web UI instead of starting it again.")
      raise SystemExit(1)
  starting_fresh = not os.path.exists(config.config_path())
  update_config(config.config_path())
  config.reload_config()
  resolve_device()
  # A new named instance only. The default instance is the first one, so "the device the
  # first instance uses" is its own -- and its new file takes the device setup.json saved,
  # so the warning told someone re-cloning to move a device that was already right.
  if starting_fresh and args.instance:
    warning(f"Created {config.config_path()} from the template. Point it at its own "
            "emulator before running it -- it currently carries the template's "
            "device_id, which is the one the first instance uses.")
  for path in config.MACHINE_CONFLICTS:
    warning(f"'{path}' is set in both {config.config_path()} and {config.MACHINE_PATH}; "
            "the machine-wide value wins. Remove it from the instance config.")
  start_server()
