"""The command line, parsed once, for everything that asks.

This lived inside `utils.log` -- so every module that wanted to know whether `--debug`
was passed imported the logging module to find out, and the logging module could not be
imported without parsing a command line. Splitting it costs nothing and ends both
oddities; `utils.log` re-exports `args`, so the existing import sites keep working.

`parse_known_args` rather than `parse_args` on purpose: devtools and the replay scripts
add their own flags and import this on the way past, and an unknown flag must not kill
the process with a usage message.
"""
import argparse
import logging

parser = argparse.ArgumentParser()

parser.add_argument('--debug', nargs='?', const=0, type=int, default=None,
                    help='Enable debug logging with optional level (default: 0)')
parser.add_argument('--save-images', action='store_true', help='Enable saving debug images')
parser.add_argument('--dry-run-turn', action='store_true', help='Dry run a single turn')
parser.add_argument('--device-debug', action='store_true', help='Enable device debug logging')
parser.add_argument('--select-skills-only', action='store_true',
                    help='Independent Training: select skills on the Learn screen but stop '
                         'before committing them, so the run can be repeated with Reset')
parser.add_argument('--pretend-tp-short', action='store_true',
                    help='Independent Training: treat every career as unaffordable, to '
                         'exercise the TP wait without an account that is really short. '
                         'Never opens Recover TP, so it cannot spend carats')
parser.add_argument('--tp-wait-seconds', type=int, default=0,
                    help='Independent Training: cap the TP wait at this many seconds, so '
                         'a four-hour hold can be watched in one. Only ever shortens it')
parser.add_argument('--use-adb', type=str, help='Specify ADB device string')

# Identity, declared rather than inferred. Without these an instance is whichever web port
# happened to be free when it started, so starting the second one while the first is down
# makes it the first -- it takes the other's log directory and, once state is keyed on it,
# the other's queue. --port pins the thing the number was being read off.
parser.add_argument('--instance', type=str, default=None,
                    help='Name this instance (logs, notifications). Default: positional')
parser.add_argument('--port', type=int, default=None,
                    help='Pin the web UI port instead of scanning 8000-8009')
parser.add_argument('--hotkey', type=str, default=None,
                    help='Pin the start/stop hotkey instead of deriving it from the port')

args, unknown = parser.parse_known_args()


def _resolve_debug_level():
  """How loud to be, and which of the debug extras `--debug N` also turns on.

  The number is a dial rather than a set of separate flags because the flags are almost
  always wanted together and in that order: 1 adds the saved frames, 2 adds the
  per-device chatter. Each step only ever switches something *on*, so passing the
  individual flag as well is never undone here.

  Levels 3 to 10 used to shorten a career by limiting turns. Independent Training has no
  turns to limit, so only the dry run above 10 survives.
  """
  if args.debug is None:
    print("[DEBUG] Setting log level to INFO")
    return logging.INFO

  print("[DEBUG] Setting log level to DEBUG")
  if args.debug > 0 and not args.save_images:
    print("[DEBUG] Setting save images to True")
    args.save_images = True
  if args.debug > 1 and not args.device_debug:
    print("[DEBUG] Setting device debug to True")
    args.device_debug = True
  if args.debug > 10 and not args.dry_run_turn:
    print("[DEBUG] Setting dry run turn to True")
    args.dry_run_turn = True
  return logging.DEBUG


log_level = _resolve_debug_level()
