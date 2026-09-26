"""The spark reroll end to end: reading the lists, deciding, asking in Discord, keeping.

Three halves.

The reader, against the captures of the 2026-09-24 reroll (untracked, references/):
every screen the list is drawn on reads as the set it shows, name for name and star for
star, and scrolling through a list reads all of it once.

The handlers, with the device, OCR and Discord stood in for: each screen presses what the
decision says, a reroll is only spent when the rules want one, the answer from Discord is
what is kept, the final dialog is backed out of when it names the other set, and the
choice is recorded beside both sets.

The Discord client, against a fake network: what it sends, and how it reads an answer --
its own reactions are not answers, and two at once is not an answer yet.

  py devtools/check_spark_reroll_flow.py
"""

import glob
import io
import json
import os
import sys
import tempfile
import urllib.error

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402
from core import asker, independent_sparks, relay_client, spark_reader  # noqa: E402
from core.spark_reader import SparkRow  # noqa: E402
from scenarios.tasks import spark_reroll  # noqa: E402
from utils.device_action_wrapper import BotStopException  # noqa: E402

CAPTURES = "references/independent_training_adb"
ORIGINAL = [("blue", "Power", 2), ("pink", "Pace Chaser", 2),
            ("green", "Dreams Donned with Pride!", 2), ("white", "Yasuda Kinen", 2),
            ("white", "Tenno Sho (Autumn)", 1), ("white", "Japan C.", 2),
            ("white", "Arima Kinen", 2), ("white", "Standard Distance ○", 1),
            ("white", "Long Straightaways ○", 2), ("white", "Long Corners ○", 2),
            ("white", "Late Surger Corners ○", 2), ("white", "Pace Chaser Savvy ○", 2),
            ("white", "Groundwork", 2), ("white", "Slipstream", 1),
            ("white", "Playtime's Over!", 1), ("white", "Restraint", 2),
            ("white", "On the Attack", 1), ("white", "On the Way to Our Dream", 1),
            ("white", "Our Grand Concert", 2)]
REROLLED = [("blue", "Stamina", 1), ("pink", "Turf", 2),
            ("green", "Dreams Donned with Pride!", 1), ("white", "Tenno Sho (Spring)", 2),
            ("white", "NHK Mile C.", 2), ("white", "Yasuda Kinen", 1),
            ("white", "Tenno Sho (Autumn)", 2), ("white", "Tokyo Daishoten", 2),
            ("white", "Prudent Positioning", 2), ("white", "Medium Corners ○", 1),
            ("white", "Groundwork", 2), ("white", "Playtime's Over!", 2),
            ("white", "On the Attack", 2), ("white", "On the Way to Our Dream", 1)]

failures = []


def check(condition, message):
  print(f"  {'PASS' if condition else 'FAIL'}  {message}")
  if not condition:
    failures.append(message)


def as_tuples(rows):
  return [(row.colour, row.name, row.stars) for row in rows]


def load(path):
  return cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)


# --- the reader -------------------------------------------------------------------------

def reader_cases():
  print("\nReading the lists off the captures:")
  if not os.path.isfile(os.path.join(CAPTURES, "spark_probe_2.png")):
    print(f"  SKIP  no captures in {CAPTURES} (untracked)")
    return
  screens = {"spark_probe_2.png": ORIGINAL[:9], "sparks_rerolled.png": REROLLED[:9],
             "spark_selection_rerolled.png": REROLLED[:9],
             "spark_selection_original.png": ORIGINAL[:9],
             "keep_sparks_original.png": ORIGINAL[:11]}
  for name, expected in screens.items():
    got = as_tuples(spark_reader.parse_spark_rows(load(os.path.join(CAPTURES, name))))
    check(got == expected, f"{name}: the rows on screen, colour, name and stars"
                           + ("" if got == expected else f" -- got {got}"))
  for prefix, expected in (("original", ORIGINAL), ("rerolled", REROLLED)):
    frames = [load(path) for path in sorted(glob.glob(
        os.path.join(CAPTURES, "sparks", f"{prefix}_*.png")))]
    if not frames:
      continue
    position = {"at": 0}                     # a list opens at its top
    scrolls = []

    def frame():
      return frames[position["at"]]

    def scroll(notches):
      scrolls.append(notches)
      step = -1 if notches > 0 else 1
      position["at"] = min(len(frames) - 1, max(0, position["at"] + step))

    rows, image = spark_reroll.read_list(spark_reroll.LIST_BAND, frame, scroll)
    check(as_tuples(rows) == expected, f"the whole {prefix} set, read once each")
    check(scrolls and all(notches < 0 for notches in scrolls),
          f"the {prefix} list is only scrolled down, never up first: {scrolls}")
    check(len(scrolls) <= len(frames) - 1 + spark_reroll.STILL_TO_END,
          f"and stops within {spark_reroll.STILL_TO_END} still scrolls of the bottom: "
          f"{len(scrolls)} scrolls for {len(frames)} frames")
    check(image[:4] == b"\x89PNG", f"and the {prefix} set comes out as one picture")


# --- the handlers -------------------------------------------------------------------------

class Fakes:
  """Everything the handlers reach outside themselves, recorded or scripted."""

  def __init__(self):
    self.clicks, self.points, self.stops, self.posts = [], [], [], []
    self.page = "rerolled"            # what Spark Selection's title reads
    self.header = "original sparks"   # what the Keep dialog's header reads
    self.reads = {}                   # band -> rows the next read_list returns
    self.answers = []                 # what answer() returns, poll by poll

  def install(self):
    self.saved = {name: getattr(spark_reroll, name) for name in
                  ("_click", "_click_point", "_stop", "read_list", "_ocr_box", "sleep")}
    self.saved_relay = {name: getattr(relay_client, name) for name in
                        ("token", "ask", "status", "settle")}
    self.saved_poll = asker.SharedBot.POLL_SECONDS
    spark_reroll._click = lambda template, *a, **k: self.clicks.append(
        os.path.basename(template)) or True
    spark_reroll._click_point = lambda x, y, text="": self.points.append(text) or True

    def stop(reason, key, message, recoverable=None):
      self.stops.append(message)
      raise BotStopException(message)
    spark_reroll._stop = stop
    spark_reroll.read_list = lambda band: self.reads[band]
    spark_reroll._ocr_box = lambda box: (self.page if box == spark_reroll.PAGE_TITLE_BOX
                                         else self.header)
    spark_reroll.sleep = lambda *a, **k: None
    relay_client.token = lambda: "tok"
    self.keys = []

    def ask(text, images, options, key, **k):
      self.keys.append(key)
      self.posts.append((text, images, options))
      return f"q{len(self.posts)}"
    relay_client.ask = ask

    def status(question_id, **k):
      picked = self.answers.pop(0) if self.answers else None
      if picked is None:
        return {"status": "pending"}
      if picked == "expired":
        return {"status": "expired"}
      return {"status": "answered", "answer": picked}
    relay_client.status = status
    relay_client.settle = lambda *a, **k: None
    asker.SharedBot.POLL_SECONDS = 0
    return self

  def restore(self):
    for name, value in self.saved.items():
      setattr(spark_reroll, name, value)
    for name, value in self.saved_relay.items():
      setattr(relay_client, name, value)
    asker.SharedBot.POLL_SECONDS = self.saved_poll


class State:
  def __init__(self, rating=17_811, bought=None, aptitudes=None):
    self.career_rating = rating
    self.skills_bought = bought
    self.aptitudes = aptitudes
    spark_reroll.reset(self)


def rows_of(spec):
  return [SparkRow(colour, name, stars, 0, name) for colour, name, stars in spec]


def setting(**changes):
  base = {"at_ss_rating": True, "any_rating": False,
          "blue": {"required": False, "sparks": [], "min_stars": 1},
          "pink": {"required": False, "sparks": [], "min_stars": 1},
          "white": {"required": True, "sparks": ["Uma Stan"]}}
  base.update(changes)
  return base


def run(handler, state, *args):
  try:
    handler(state, *args)
  except BotStopException:
    pass


def fake_wait(seconds, screen, label, still_waiting=None):
  for _ in range(10):
    if not still_waiting():
      return False
  return True


def handler_cases():
  print("\nDeciding on the first set:")
  saved = (getattr(config, "INDEPENDENT_SPARK_REROLL", None),
           getattr(config, "INDEPENDENT_DEBUG_STOP_BEFORE_SPARK_REROLL", False))
  fakes = Fakes().install()
  image = b"\x89PNG-original"
  fakes.reads[spark_reroll.LIST_BAND] = (rows_of(ORIGINAL), image)
  held = ["Superstan"]                        # Uma Stan is possible, and not granted
  try:
    # As shipped: no colour rules, so the trigger alone decides.
    independent_sparks.COLOUR_RULES = False
    config.INDEPENDENT_SPARK_REROLL = setting(white={"required": True,
                                                     "sparks": ["Groundwork"]})
    state = State(bought=["Groundwork"])
    run(spark_reroll.handle_sparks, state)
    check(state.spark_decision == "reroll" and fakes.clicks == ["spark_reroll_btn.png"],
          "colour rules off: SS is rerolled though the set has what was required")
    fakes.clicks.clear()
    config.INDEPENDENT_SPARK_REROLL = setting(at_ss_rating=False, any_rating=True,
                                              white={"required": False, "sparks": []})
    state = State(rating=9_000)
    run(spark_reroll.handle_sparks, state)
    check(state.spark_decision == "reroll",
          "colour rules off: any_rating rerolls with no colour chosen at all")
    fakes.clicks.clear()
    config.INDEPENDENT_SPARK_REROLL = setting(at_ss_rating=False)
    state = State(bought=held)
    run(spark_reroll.handle_sparks, state)
    check(fakes.clicks == ["confirm_btn.png"] and state.spark_decision is None,
          "colour rules off: with no trigger on, Sparks is confirmed")
    fakes.clicks.clear()
    independent_sparks.COLOUR_RULES = True

    config.INDEPENDENT_SPARK_REROLL = setting(at_ss_rating=False)
    state = State(bought=held)
    run(spark_reroll.handle_sparks, state)
    check(fakes.clicks == ["confirm_btn.png"] and state.spark_decision is None,
          "with no trigger on, Sparks is confirmed as it always was")

    config.INDEPENDENT_SPARK_REROLL = setting()
    fakes.clicks.clear()
    state = State(bought=held)
    run(spark_reroll.handle_sparks, state)
    check(state.spark_decision == "reroll" and fakes.clicks == ["spark_reroll_btn.png"],
          "SS, and the required Uma Stan missing: Reroll Sparks is pressed")
    check(state.spark_sets["original"] == (rows_of(ORIGINAL), image) or
          as_tuples(state.spark_sets["original"][0]) == ORIGINAL,
          "and the first set is kept for the question later")

    fakes.clicks.clear()
    state = State(rating=17_000, bought=held)
    run(spark_reroll.handle_sparks, state)
    check(state.spark_decision == "keep" and fakes.clicks == ["confirm_btn.png"],
          "below SS with only the SS trigger on, the sparks are kept")

    fakes.clicks.clear()
    state = State(bought=["Groundwork"])     # nothing that brings Uma Stan
    run(spark_reroll.handle_sparks, state)
    check(state.spark_decision == "keep" and fakes.clicks == ["confirm_btn.png"],
          "a wanted white the career could never be granted is no reason to reroll")

    config.INDEPENDENT_SPARK_REROLL = setting(white={"required": True,
                                                     "sparks": ["Groundwork"]})
    fakes.clicks.clear()
    state = State(bought=["Groundwork"])
    run(spark_reroll.handle_sparks, state)
    check(state.spark_decision == "keep", "a set that already has what is wanted is kept")

    config.INDEPENDENT_SPARK_REROLL = setting()
    fakes.reads[spark_reroll.LIST_BAND] = (
        rows_of(ORIGINAL[:3]) + [SparkRow("white", "Xq Zzv Pqlw", 2, 0, "Xq Zzv Pqlw")],
        image)
    fakes.clicks.clear()
    state = State(bought=held)
    run(spark_reroll.handle_sparks, state)
    check(state.spark_decision == "keep",
          "a white that cannot be read stops a reroll on a guess")
    fakes.reads[spark_reroll.LIST_BAND] = (rows_of(ORIGINAL), image)

    config.INDEPENDENT_DEBUG_STOP_BEFORE_SPARK_REROLL = True
    fakes.clicks.clear()
    state = State(bought=held)
    run(spark_reroll.handle_sparks, state)
    check(fakes.stops and "spark_reroll_btn.png" not in fakes.clicks,
          "the debug switch stops before Reroll Sparks, spending nothing")
    config.INDEPENDENT_DEBUG_STOP_BEFORE_SPARK_REROLL = False

    fakes.clicks.clear()
    state = State(bought=held)
    for _ in range(spark_reroll.MAX_REROLL_PRESSES + 1):
      run(spark_reroll.handle_sparks, state)
    check(fakes.clicks.count("spark_reroll_btn.png") == spark_reroll.MAX_REROLL_PRESSES
          and fakes.clicks[-1] == "confirm_btn.png",
          "Reroll Sparks is not pressed for ever when the sparks never change")

    relay_client.token = lambda: ""
    fakes.clicks.clear()
    state = State(bought=held)
    run(spark_reroll.handle_sparks, state)
    check(fakes.clicks == ["confirm_btn.png"],
          "with the Mirako bot not linked, the sparks are kept, not rerolled")
    relay_client.token = lambda: "tok"

    print("\nThe screens in between:")
    fakes.clicks.clear()
    state = State(bought=held)
    state.spark_decision = "reroll"
    run(spark_reroll.handle_reroll_confirm, state)
    state.spark_decision = "keep"
    run(spark_reroll.handle_reroll_confirm, state)
    check(fakes.clicks == ["spark_reroll_dialog_btn.png", "cancel_btn.png"],
          "the confirm dialog rerolls only when that was decided, and backs out otherwise")
    fakes.reads[spark_reroll.LIST_BAND] = (rows_of(REROLLED), b"\x89PNG-rerolled")
    fakes.clicks.clear()
    run(spark_reroll.handle_sparks_rerolled, state)
    check(as_tuples(state.spark_sets["rerolled"][0]) == REROLLED
          and fakes.clicks == ["next_btn.png"], "the rerolled set is read, then Next")

    print("\nAsking, and keeping the answer:")
    saved_list = getattr(config, "SKILL_LIST", [])
    config.SKILL_LIST = ["Superstan", "Groundwork", "Long Corners ◎", "Prudent Positioning"]
    state = State(bought=["Long Corners ○", "Superstan", "Slipstream", "Groundwork"])
    state.spark_sets = {"original": (rows_of(ORIGINAL + [("white", "Uma Stan", 1)]),
                                     b"\x89PNG-o"),
                        "rerolled": (rows_of(REROLLED), b"\x89PNG-r")}
    fakes.clicks.clear()
    fakes.posts.clear()
    fakes.page = "rerolled sparks"
    fakes.answers = [None, None, "original"]
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check(len(fakes.posts) == 1 and len(fakes.posts[0][1]) == 2,
          "one question, with both sets as pictures")
    check("Power" in fakes.posts[0][0] and "Turf" in fakes.posts[0][0],
          "the message lists both sets too")
    text = fakes.posts[0][0]
    check("Important skills (3): Uma Stan ★ (Superstan), Groundwork ★★, Long Corners ○ ★★ "
          "(Long Corners ◎)" in text,
          "the original set's overlap with the priority list, in its order, a spark "
          "from an upgrade naming the skill it came with -- got "
          + repr([line for line in text.splitlines() if line.startswith("Important")]))
    check("Important skills (2): Groundwork ★★, Prudent Positioning ★★" in text,
          "and the rerolled set's, under its own summary")
    check(text.index("Important skills (3)") < text.index("Rerolled")
          < text.index("Important skills (2)"), "each overlap sits below its own set")
    check("Priority" not in text, "the choice between sets has no Priority line")
    check("React to answer" not in fakes.posts[0][0]
          and [o["id"] for o in fakes.posts[0][2]] == ["original", "rerolled"]
          and fakes.posts[0][2][0]["label"] == "Keep the original",
          "the Mirako bot is given the options as buttons, not reaction instructions")
    config.SKILL_LIST = saved_list
    check(state.spark_choice == "original",
          "the answer is taken once a button is pressed, however many polls it takes")
    check(fakes.points and not fakes.clicks,
          "showing the reroll page, it turns to the original rather than confirming")
    fakes.page = "original sparks"
    fakes.points.clear()
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check(fakes.clicks == ["confirm_btn.png"] and len(fakes.posts) == 1,
          "on the right page it confirms, without asking again")

    fakes.clicks.clear()
    fakes.header = "rerolled sparks"
    run(spark_reroll.handle_keep_sparks, state)
    check(fakes.clicks == ["cancel_btn.png"],
          "a final dialog naming the other set is backed out of")
    fakes.clicks.clear()
    fakes.header = "original sparks"
    with tempfile.TemporaryDirectory() as folder:
      path = os.path.join(folder, "spark_choices.jsonl")
      saved_record = spark_reroll.record_choice
      spark_reroll.record_choice = lambda st: saved_record(st, path)
      try:
        run(spark_reroll.handle_keep_sparks, state)
        run(spark_reroll.handle_keep_sparks, state)
      finally:
        spark_reroll.record_choice = saved_record
      lines = io.open(path, encoding="utf-8").read().splitlines()
      entry = json.loads(lines[0])
    check(fakes.clicks == ["confirm_btn.png", "confirm_btn.png"],
          "and the one naming the chosen set is confirmed")
    check(len(lines) == 1 and entry["choice"] == "original" and entry["rating"] == 17_811
          and len(entry["original"]) == 20 and len(entry["rerolled"]) == 14,
          "the choice is recorded once, beside both sets")

    print("\nComing back to Spark Selection after a restart:")
    state = State()
    fakes.page = "rerolled sparks"
    fakes.reads[spark_reroll.SELECTION_BAND] = (rows_of(REROLLED), b"\x89PNG-r")
    fakes.points.clear()
    fakes.posts.clear()
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check("rerolled" in state.spark_sets and fakes.points and not fakes.posts,
          "the set on screen is read, then the page turned for the other")
    fakes.page = "original sparks"
    fakes.reads[spark_reroll.SELECTION_BAND] = (rows_of(ORIGINAL), b"\x89PNG-o")
    fakes.answers = ["rerolled"]
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check(len(fakes.posts) == 1 and state.spark_choice == "rerolled",
          "with both read, the question is asked and answered")

    print("\nThe relay's key, per question:")
    state = State()
    state.spark_sets = {"original": (rows_of(ORIGINAL), b"\x89PNG-o"),
                        "rerolled": (rows_of(REROLLED), b"\x89PNG-r")}
    fakes.page = "original sparks"
    fakes.keys.clear()
    real_ask = relay_client.ask
    relay_client.ask = lambda *a, **k: (_ for _ in ()).throw(
        relay_client.RelayError("Could not reach the relay: timed out"))
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    relay_client.ask = real_ask
    fakes.answers = ["original"]
    first_key = state.spark_ask_key
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check(fakes.keys == [first_key] and state.spark_choice == "original",
          "a question retried after a failure goes out under the career's key")
    abandoned = State()
    check(abandoned.spark_ask_key != first_key,
          "a new career has a key of its own, so a question left behind is never reused")

    print("\nA question that can no longer be answered:")
    state = State()
    state.spark_sets = {"original": (rows_of(ORIGINAL), b"\x89PNG-o"),
                        "rerolled": (rows_of(REROLLED), b"\x89PNG-r")}
    fakes.page = "original sparks"
    fakes.posts.clear()
    fakes.answers = [None, "expired"]
    fakes.keys.clear()
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check(state.spark_question_id is None and state.spark_choice is None,
          "an expired question is dropped, with nothing chosen")
    fakes.answers = ["original"]
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check(len(fakes.posts) == 2 and state.spark_choice == "original",
          "and the next pass asks again and takes that answer")
    check(len(fakes.keys) == 2 and fakes.keys[0] != fakes.keys[1],
          "under a new key: the old one would get the expired question back")
  finally:
    fakes.restore()
    config.INDEPENDENT_SPARK_REROLL, config.INDEPENDENT_DEBUG_STOP_BEFORE_SPARK_REROLL = saved


# --- the relay and notifications -------------------------------------------------------------------

class Reply:
  def __init__(self, body):
    self.body = body

  def read(self):
    return self.body

  def __enter__(self):
    return self

  def __exit__(self, *_):
    return False


def notification_cases():
  print("\nThe asker:")
  from core import asker
  check(isinstance(asker.backend(), asker.SharedBot),
        "questions go through the Mirako bot")
  print("\nNotifications, when DMs are set up:")
  import utils.notifications as notifications
  import utils.webhook as webhook

  class Recorder:
    def __init__(self):
      self.items = []

    def put(self, item):
      self.items.append(item)

  names = ("WEBHOOK_URL", "WEBHOOK_CAREER_SUMMARY_ENABLED", "WEBHOOK_RELAY_TOKEN")
  saved = {name: getattr(config, name, None) for name in names}
  real_queue = webhook._delivery_queue
  queued = Recorder()
  webhook._delivery_queue = queued
  try:
    config.WEBHOOK_URL = "https://discord.com/api/webhooks/1/x"
    config.WEBHOOK_RELAY_TOKEN = ""
    config.WEBHOOK_CAREER_SUMMARY_ENABLED = True
    webhook.send_started()
    check(queued.items and queued.items[-1][0] == config.WEBHOOK_URL,
          "not linked, notifications use the webhook as before")
    config.WEBHOOK_RELAY_TOKEN = "tok"
    webhook.send_started()
    check(queued.items[-1][0] is webhook._DM,
          "linked, they go to the DMs instead of the webhook")
    config.WEBHOOK_URL = ""
    check(notifications._webhook_enabled(), "DMs alone count as notifications being on")
    before = len(queued.items)
    notifications.on_career_complete({"rating": 17811}, 1)
    check(len(queued.items) == before + 1 and queued.items[-1][0] is webhook._DM,
          "a career result goes to the DMs with no webhook set at all")
    config.WEBHOOK_CAREER_SUMMARY_ENABLED = False
    notifications.on_career_complete({"rating": 17811}, 1)
    check(len(queued.items) == before + 1, "and its own switch still turns it off")
  finally:
    webhook._delivery_queue = real_queue
    for name, value in saved.items():
      setattr(config, name, value)


class Script:
  """A fake relay: answers each call with the next scripted reply, recording the calls.
  A reply is (status, body) or an exception to raise."""

  def __init__(self, *replies):
    self.replies = list(replies)
    self.calls = []

  def __call__(self, request, timeout=None):
    self.calls.append(request)
    reply = self.replies.pop(0)
    if isinstance(reply, Exception):
      raise reply
    status, body = reply
    data = json.dumps(body).encode() if body is not None else b""
    if status >= 400:
      raise urllib.error.HTTPError(request.full_url, status, "no", {}, io.BytesIO(data))
    return Reply(data)


def relay_cases():
  print("\nThe relay client:")
  saved = getattr(config, "WEBHOOK_RELAY_TOKEN", None)
  saved_fns = {name: getattr(relay_client, name) for name in ("ask", "status", "settle")}
  try:
    config.WEBHOOK_RELAY_TOKEN = ""
    check(not asker.SharedBot().configured(), "not linked with no token saved")
    config.WEBHOOK_RELAY_TOKEN = "tok"
    check(asker.SharedBot().configured(), "linked once a token is saved")

    relay = Script((200, {"token": "new", "user": {"id": "1", "name": "mira"}}))
    reply = relay_client.link(" k7q2m9xa ", opener=relay)
    request = relay.calls[0]
    check(reply["token"] == "new" and request.full_url.endswith("/v1/link")
          and json.loads(request.data)["code"] == "K7Q2M9XA"
          and request.get_header("Authorization") is None,
          "a link code is sent tidied, and without a token")
    relay = Script((400, {"error": "bad_code", "message": "That code has expired."}))
    try:
      relay_client.link("x", opener=relay)
      check(False, "a bad code is refused")
    except relay_client.RelayError as error:
      check(str(error) == "That code has expired." and error.permanent,
            "a refused code comes back in the relay's own words, and is not retried")

    relay = Script((200, {"user": {"name": "mira"}}))
    relay_client.me(opener=relay)
    check(relay.calls[0].get_header("Authorization") == "Bearer tok",
          "every other call carries the saved token")
    relay = Script((204, None))
    relay_client.test(auth="typed", opener=relay)
    check(relay.calls[0].get_header("Authorization") == "Bearer typed",
          "or the one the page passes, before it is saved")

    # Asking: the key is the caller's, one per question.
    keys = []

    def ask(text, images, options, key, **k):
      keys.append(key)
      return "q7"
    relay_client.ask = ask
    options = spark_reroll.OPTIONS
    asker.SharedBot().ask("which?", [], options, key="K1")
    asker.SharedBot().ask("which?", [], options, key="K1")
    check(keys == ["K1", "K1"], "the key the caller gives is the one sent, retry after retry")
    asker.SharedBot().ask("new", [], options)
    asker.SharedBot().ask("new", [], options)
    check(keys[2] and keys[3] and keys[2] != keys[3] and "K1" not in keys[2:],
          "and with none given, each call is a question of its own")
    check(not hasattr(asker.SharedBot, "_pending_key"),
          "no key is kept on the asker, which outlives careers")

    texts = []
    relay_client.ask = lambda text, *a, **k: texts.append(text) or "q8"
    asker.SharedBot().ask("x" * 3000, [], options)
    check(len(texts[0]) <= asker.SharedBot.TEXT_LIMIT, "text is cut to the relay's limit")

    real = relay_client.ask
    body = {}

    def capture(request, timeout=None):
      body["data"] = request.data
      body["key"] = request.get_header("Idempotency-key")
      return Reply(b'{"id": "q9"}')
    relay_client.ask = saved_fns["ask"]
    got = relay_client.ask("t", [("original.png", b"PNGDATA")],
                           [{"id": "original", "label": "Keep", "emoji": "1"}], "K1",
                           opener=capture)
    check(got == "q9" and b'name="files[0]"; filename="original.png"' in body["data"]
          and b"PNGDATA" in body["data"] and b'"id": "original"' in body["data"]
          and body["key"] == "K1",
          "the question goes as multipart: payload, pictures and the key")
    relay_client.ask = real

    # Reading the answer back.
    polls = []

    def status(question_id, **k):
      polls.append(question_id)
      return {"status": "answered", "answer": "rerolled"}
    relay_client.status = status
    asker.SharedBot._last_poll.clear()
    clock = iter([100.0, 104.0, 111.0])
    bot = asker.SharedBot()
    first = bot.answer("q1", options, clock=lambda: next(clock))
    second = bot.answer("q1", options, clock=lambda: next(clock))
    third = bot.answer("q1", options, clock=lambda: next(clock))
    check(first == "rerolled" and second is None and third == "rerolled" and len(polls) == 2,
          "polled no more than once every 10 seconds, as the relay allows")

    def gone(question_id, **k):
      raise relay_client.RelayError("This Mirako Machine is not linked.", 401, "unlinked")
    relay_client.status = gone
    asker.SharedBot._last_poll.clear()
    try:
      asker.SharedBot().answer("q2", options)
      check(False, "an unlinked token ends the wait")
    except asker.AskGone:
      check(True, "an unlinked token ends the wait instead of polling for ever")
    except asker.AskError:
      check(False, "an unlinked token ends the wait instead of polling for ever")

    def busy(question_id, **k):
      raise relay_client.RelayError("The relay cannot reach Discord right now.", 503)
    relay_client.status = busy
    asker.SharedBot._last_poll.clear()
    try:
      asker.SharedBot().answer("q3", options)
    except asker.AskGone:
      check(False, "a relay that is only busy does not drop the question")
    except asker.AskError:
      check(True, "a relay that is only busy keeps the question, to poll again")

    class Html:
      def __init__(self, *_):
        pass

      def __call__(self, request, timeout=None):
        return Reply(b"<html>Bad gateway</html>")
    try:
      relay_client.me(opener=Html())
      check(False, "a reply that is not JSON is refused")
    except relay_client.RelayError as error:
      check(not error.permanent and "usual reply" in str(error),
            "a success reply that is not JSON is a relay error that may pass, not a crash")

    relay = Script((202, None))
    relay_client.notify([{"title": "Career 1 Complete"}], opener=relay)
    check(relay.calls[0].full_url.endswith("/v1/notifications")
          and json.loads(relay.calls[0].data) == {"embeds": [{"title": "Career 1 Complete"}]},
          "a notification is forwarded as its embeds")
  finally:
    config.WEBHOOK_RELAY_TOKEN = saved
    for name, value in saved_fns.items():
      setattr(relay_client, name, value)
    asker.SharedBot._last_poll.clear()


def ask_first_cases():
  print("\nAsking before the reroll (ask_first):")
  saved = (getattr(config, "INDEPENDENT_SPARK_REROLL", None), getattr(config, "SKILL_LIST", []),
           spark_reroll.record_choice, independent_sparks.COLOUR_RULES)
  independent_sparks.COLOUR_RULES = False     # as shipped
  fakes = Fakes().install()
  image = b"\x89PNG-original"
  fakes.reads[spark_reroll.LIST_BAND] = (rows_of(ORIGINAL), image)
  recorded = []
  try:
    config.SKILL_LIST = ["Groundwork", "Long Corners ◎"]
    config.INDEPENDENT_SPARK_REROLL = setting(ask_first=True,
                                              white={"required": False, "sparks": []})
    check(independent_sparks.settings()["ask_first"], "the setting is read")
    with tempfile.TemporaryDirectory() as folder:
      path = os.path.join(folder, "spark_choices.jsonl")
      spark_reroll.record_choice = lambda st: saved[2](st, path)

      state = State(bought=["Long Corners ○", "Groundwork", "Slipstream"])
      fakes.answers = [None, None, "reroll"]
      run(spark_reroll.handle_sparks, state, fake_wait)
      text, images, options = fakes.posts[0]
      check(len(fakes.posts) == 1 and [o["id"] for o in options] == ["reroll", "keep"],
            "a career a trigger allows is asked about, with Reroll and Keep")
      check("rating 17,811" in text.lower() and "Blue: Power ★★" in text
            and "Pink: Pace Chaser ★★" in text,
            f"the question gives the rating, blue and pink: {text!r}")
      whites = [(name, stars) for colour, name, stars in ORIGINAL if colour == "white"]
      listed = ", ".join(f"{name} {'★' * stars}" for name, stars in whites)
      check("Important skills (2): Groundwork ★★, Long Corners ○ ★★ (Long Corners ◎)"
            in text, "the whites that match the priority list")
      check("Priority (2 of 2): Groundwork, Long Corners ○ (Long Corners ◎)" in text
            and text.index("Important skills") < text.index("Priority (2 of 2)"),
            f"then the priority skills bought, not the sparks: {text!r}")
      check(spark_reroll._priority_line(State()) == "Priority: not known"
            and spark_reroll._priority_line(State(bought=[])) == "Priority (0 of 2): none",
            "unknown after a restart, and none when nothing on the list was bought")
      check(text.rstrip().endswith(f"White: {listed} ({len(whites)} total)")
            and text.index("Priority (2") < text.index("White:"),
            "then every white in detail, with the total after it, last")
      check(images == [("sparks.png", image)], "with the first set's picture")
      check(state.spark_decision == "reroll" and fakes.clicks == ["spark_reroll_btn.png"],
            "answered Reroll, the reroll goes ahead as usual")
      reroll_key = fakes.keys[0]
      check(reroll_key != state.spark_ask_key,
            "under a key of its own, so the keep question later is not handed this one")

      fakes.clicks.clear()
      fakes.posts.clear()
      state = State()
      fakes.answers = ["keep"]
      run(spark_reroll.handle_sparks, state, fake_wait)
      check(state.spark_decision == "keep" and fakes.clicks == ["confirm_btn.png"],
            "answered Keep, the sparks are confirmed and nothing is spent")
      entry = json.loads(io.open(path, encoding="utf-8").read().splitlines()[-1])
      check(entry["reroll_answer"] == "keep" and entry["choice"] is None,
            "and the answer is recorded, for a rule to learn from later")

      fakes.clicks.clear()
      fakes.posts.clear()
      state = State()
      fakes.answers = []
      run(spark_reroll.handle_sparks, state, fake_wait)
      check(state.spark_decision == "ask" and not fakes.clicks and len(fakes.posts) == 1,
            "unanswered, it stays on the Sparks screen and presses nothing")
      run(spark_reroll.handle_sparks, state, fake_wait)
      check(len(fakes.posts) == 1 and not fakes.clicks,
            "and the next pass waits on the same question rather than asking again")

      config.INDEPENDENT_SPARK_REROLL = setting(ask_first=True, at_ss_rating=True)
      fakes.clicks.clear()
      fakes.posts.clear()
      state = State(rating=17_000)
      run(spark_reroll.handle_sparks, state, fake_wait)
      check(not fakes.posts and fakes.clicks == ["confirm_btn.png"],
            "a career no trigger allows is not asked about")

      import scenarios.independent_training as independent
      from scenarios.independent_screens import Screen
      seen = {}
      real_handle = spark_reroll.handle_sparks
      spark_reroll.handle_sparks = lambda st, wait=None: seen.update(wait=wait)
      try:
        independent.HANDLERS[Screen.SPARKS](State())
      finally:
        spark_reroll.handle_sparks = real_handle
      check(seen.get("wait") is independent._wait_on_screen,
            "the loop's Sparks handler hands over its wait, which the question needs")

      config.INDEPENDENT_SPARK_REROLL = setting(ask_first=False)
      fakes.clicks.clear()
      state = State()
      run(spark_reroll.handle_sparks, state, fake_wait)
      check(not fakes.posts and fakes.clicks == ["spark_reroll_btn.png"],
            "with the option off, the reroll is decided without asking, as before")
  finally:
    fakes.restore()
    (config.INDEPENDENT_SPARK_REROLL, config.SKILL_LIST, spark_reroll.record_choice,
     independent_sparks.COLOUR_RULES) = saved


def main():
  reader_cases()
  handler_cases()
  ask_first_cases()
  notification_cases()
  relay_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The sparks are read, rerolled when wanted, and kept as answered.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
