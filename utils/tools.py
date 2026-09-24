"""Waiting, scaled by the user's speed setting.

Everything the bot waits for is scaled by `sleep_time_multiplier`, so a slow emulator
can be given more room without touching a hundred call sites. Two functions, because a
wait is either taken here or handed to something that does its own waiting:

  sleep(2)      -- wait two scaled seconds
  get_secs(2)   -- two scaled seconds, as a number to pass on

This module used to carry a click helper, a scroll helper, aptitude comparison and a
fuzzy matcher as well. Nothing imported any of them: clicking went to
`utils.device_action_wrapper`, the race helpers went with the career mode this build
does not have, and the fuzzy matcher was replaced by the one in `core.independent_skill`
that knows about skill names. They were removed on 2026-09-20 rather than carried.
"""
import inspect
import time

import core.config as config
from utils.log import debug, args


def scaled(seconds):
  """`seconds` in real time, after the user's multiplier."""
  return seconds * config.SLEEP_TIME_MULTIPLIER


def sleep(seconds=1):
  """Wait `seconds`, scaled.

  The trace of who asked sits behind `--device-debug` rather than `--debug`: this runs
  hundreds of times in a career, and `inspect.stack()` costs more to build the message
  than the logging costs to write it.
  """
  if args.device_debug:
    debug(f"sleep called from {inspect.stack()[1].function} for {seconds} seconds")
  time.sleep(scaled(seconds))


def get_secs(seconds=1):
  """`seconds`, scaled, for a caller that does its own waiting -- a search timeout, say."""
  return scaled(seconds)
