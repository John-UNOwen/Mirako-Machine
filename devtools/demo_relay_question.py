"""Send yourself a demo spark question through Mirako Relay and time the press.

Discord only creates a button press when someone clicks in a Discord client, so a press
cannot be faked. This asks a real question instead -- the same Reroll / Keep buttons the
spark reroll asks with, through the link saved in the web UI -- and reports which press
reached the relay and how long after the question it arrived. Nothing happens in the
game. Each question counts against the relay's daily limit (20 a player).

A press Discord answers with "This interaction failed" that never shows up here never
reached the relay; one that shows up late was delayed on the way.

  py devtools/demo_relay_question.py              one question
  py devtools/demo_relay_question.py --rounds 3   three, one after another
"""

import argparse
import os
import sys
import time
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402
from core import asker, relay_client  # noqa: E402
from scenarios.tasks.spark_reroll import REROLL_OPTIONS  # noqa: E402

# The relay allows one status read per question every 10 seconds.
POLL_SECONDS = 10.5


def stamp():
  return datetime.now().strftime("%H:%M:%S")


def one_round(number, rounds, timeout):
  bot = asker.SharedBot()
  text = (f"🧪 **Demo question {number}/{rounds}** from Mirako Machine\n"
          "Nothing happens in the game. Press either button: the script on your PC "
          "reports which press reached the relay, and when.")
  asked = time.monotonic()
  question = bot.ask(text, [], REROLL_OPTIONS, key=uuid.uuid4().hex)
  print(f"[{stamp()}] Question {number} sent ({question}). Press a button in Discord.")
  while time.monotonic() - asked < timeout:
    time.sleep(POLL_SECONDS)
    try:
      reply = relay_client.status(question) or {}
    except relay_client.RelayError as error:
      print(f"[{stamp()}] Could not read the answer yet: {error}")
      continue
    if reply.get("status") == "answered":
      label = next((o.label for o in REROLL_OPTIONS if o.id == reply.get("answer")),
                   reply.get("answer"))
      waited = time.monotonic() - asked
      print(f"[{stamp()}] Received: {label} (read {waited:.0f}s after asking; the check "
            f"runs every {POLL_SECONDS:.0f}s).")
      bot.finish(question, f"{text}\n\n✅ Demo: the relay received **{label}**.")
      return True
    if reply.get("status") == "expired":
      print(f"[{stamp()}] The question expired.")
      return False
  print(f"[{stamp()}] No press arrived within {timeout}s. If Discord showed an error for "
        "a press, that press never reached the relay.")
  bot.finish(question, f"{text}\n\n⌛ Demo: no press arrived within {timeout}s.")
  return False


def main():
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--rounds", type=int, default=1, help="questions to ask in turn")
  parser.add_argument("--timeout", type=int, default=600,
                      help="seconds to wait for each press (default 600)")
  args = parser.parse_args()
  config.reload_config()
  if not relay_client.token():
    print("The Mirako bot is not linked. Link it in the web UI first "
          "(Automation, Discord Notifications, Mirako Bot).")
    return 1
  try:
    me = relay_client.me()
    print(f"[{stamp()}] Linked to {me.get('user', {}).get('name', 'your Discord')}; "
          f"relay {relay_client.base_url()}.")
  except relay_client.RelayError as error:
    print(f"The relay refused the link: {error}")
    return 1
  received = 0
  for number in range(1, args.rounds + 1):
    try:
      received += one_round(number, args.rounds, args.timeout)
    except asker.AskError as error:
      print(f"[{stamp()}] Could not ask: {error}")
      return 1
  print(f"{received} of {args.rounds} press(es) received.")
  return 0 if received == args.rounds else 1


if __name__ == "__main__":
  sys.exit(main())
