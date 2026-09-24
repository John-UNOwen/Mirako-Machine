"""Restarting the bot after an update or a rollback.

An update moves the files under a process that keeps running the old code, so it has to
end and start again. That used to be left to the user ("close this window and run
start.bat again"), which a closed-over console or a missed message turned into a bot
serving a new page from old routes. Now the process restarts itself.

The dependencies are installed by a small script that runs *after* this process has
exited, never by this process: on Windows a running Python holds every package it has
imported open (pillow, pydantic_core, numpy, cv2), and pip cannot replace a file that is
open, so an install from inside the bot left the environment half-upgraded. The script is
written by the code that is running now, rather than taken from the checkout, because a
rollback can land on a start.bat that knows nothing about any of this.

Sibling instances share the checkout and the venv, so they restart too: each is asked to
exit, and the relaunched default instance starts them again from `RELAUNCH_PATH`.
"""
import io
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join("logs", "restart.bat")
RELAUNCH_PATH = os.path.join("config", "relaunch.json")
STAMP_PATH = os.path.join(".venv", "requirements.installed")

# How long the script waits for the old process to exit before starting anyway, seconds.
WAIT_SECONDS = 60


def script(pid, python, arguments, install, root=REPO_ROOT):
  """The batch file that waits for `pid` to exit, installs if asked, then starts main.py.

  `python` is the interpreter this process runs under, so the restart uses the same
  environment -- the venv start.bat made, or whatever the user started it with.
  """
  command = subprocess.list2cmdline([python, "main.py", *arguments])
  pip = subprocess.list2cmdline([python, "-m", "pip", "install", "-r", "requirements.txt"])
  own_venv = os.path.normcase(os.path.abspath(python)).startswith(
      os.path.normcase(os.path.join(os.path.abspath(root), ".venv")))
  lines = [
    "@echo off",
    f'cd /d "{root}"',
    "echo Restarting Mirako Machine...",
    "set /a tries=0",
    ":wait",
    # By full path: with Git's Unix tools on PATH, a bare `find` is Git's, which fails
    # on every call and made the wait end at once, with the old process still running.
    rf'"%SystemRoot%\System32\tasklist.exe" /FI "PID eq {int(pid)}" 2>nul'
    rf' | "%SystemRoot%\System32\find.exe" "{int(pid)}" >nul',
    "if errorlevel 1 goto :gone",
    "set /a tries+=1",
    f"if %tries% geq {WAIT_SECONDS} goto :gone",
    r'"%SystemRoot%\System32\PING.EXE" -n 2 127.0.0.1 >nul',
    "goto :wait",
    ":gone",
  ]
  if install:
    lines += [
      "echo Installing updated dependencies.",
      pip,
      "if errorlevel 1 (",
      "  echo.",
      "  echo   Installing the dependencies failed. Run start.bat again; if it keeps",
      "  echo   failing, the lines above say which package broke.",
      "  pause",
      "  exit /b 1",
      ")",
    ]
    if own_venv:
      # What start.bat compares against, so it does not install the same thing again.
      lines.append(f'copy /y requirements.txt "{STAMP_PATH}" >nul')
  lines.append(command)
  return "\r\n".join(lines) + "\r\n"


def write_relaunch(names, root=REPO_ROOT):
  """Record the named instances the next default instance should start again."""
  path = os.path.join(root, RELAUNCH_PATH)
  if not names:
    return
  with io.open(path, "w", encoding="utf-8") as handle:
    json.dump({"instances": sorted(set(names))}, handle)


def take_relaunch(root=REPO_ROOT):
  """The names `write_relaunch` recorded, removing the record. [] when there is none.

  Removed before anything is launched, so an instance that fails to start is not tried
  again on every later startup.
  """
  path = os.path.join(root, RELAUNCH_PATH)
  try:
    with io.open(path, encoding="utf-8") as handle:
      document = json.load(handle)
  except (OSError, ValueError):
    return []
  finally:
    try:
      os.remove(path)
    except OSError:
      pass
  names = document.get("instances") if isinstance(document, dict) else None
  return [name for name in names or [] if isinstance(name, str) and name]


def spawn(arguments, install, pid=None, python=None, popen=subprocess.Popen, root=REPO_ROOT):
  """Write the restart script and start it in a console of its own. Returns its path."""
  path = os.path.join(root, SCRIPT_PATH)
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with io.open(path, "w", encoding="utf-8", newline="") as handle:
    handle.write(script(pid if pid is not None else os.getpid(), python or sys.executable,
                        arguments, install, root))
  flags = 0
  if os.name == "nt":
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_BREAKAWAY_FROM_JOB
  try:
    popen(["cmd", "/c", path], cwd=root, creationflags=flags, close_fds=True)
  except OSError:
    # Inside a job that forbids breakaway; the console then lives with that job.
    popen(["cmd", "/c", path], cwd=root,
          creationflags=flags & ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
          close_fds=True)
  return path
