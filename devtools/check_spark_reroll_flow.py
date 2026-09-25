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
from core import discord_choice, spark_reader  # noqa: E402
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
    position = {"at": len(frames) - 1}       # start scrolled down: the reader goes up first

    def frame():
      return frames[position["at"]]

    def scroll(notches):
      step = -1 if notches > 0 else 1
      position["at"] = min(len(frames) - 1, max(0, position["at"] + step))

    rows, image = spark_reroll.read_list(spark_reroll.LIST_BAND, frame, scroll)
    check(as_tuples(rows) == expected,
          f"the whole {prefix} set, scrolled through from wherever it was left, once each")
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
    self.saved_discord = {name: getattr(discord_choice, name) for name in
                          ("post", "offer", "whoami", "answer", "edit", "configured")}
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
    discord_choice.configured = lambda: True
    discord_choice.whoami = lambda **k: {"id": "bot"}
    discord_choice.post = lambda text, images=(), **k: self.posts.append((text, images)) or "m1"
    discord_choice.offer = lambda *a, **k: None
    discord_choice.edit = lambda *a, **k: None
    discord_choice.answer = lambda *a, **k: self.answers.pop(0) if self.answers else None
    return self

  def restore(self):
    for name, value in self.saved.items():
      setattr(spark_reroll, name, value)
    for name, value in self.saved_discord.items():
      setattr(discord_choice, name, value)


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

    discord_choice.configured = lambda: False
    fakes.clicks.clear()
    state = State(bought=held)
    run(spark_reroll.handle_sparks, state)
    check(fakes.clicks == ["confirm_btn.png"],
          "with no Discord bot to ask, the sparks are kept, not rerolled")
    discord_choice.configured = lambda: True

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
    config.SKILL_LIST = ["Groundwork", "Uma Stan", "Superstan", "Long Corners ◎"]
    state = State(bought=["Long Corners ○", "Superstan", "Slipstream", "Groundwork"])
    state.spark_sets = {"original": (rows_of(ORIGINAL), b"\x89PNG-o"),
                        "rerolled": (rows_of(REROLLED), b"\x89PNG-r")}
    fakes.clicks.clear()
    fakes.posts.clear()
    fakes.page = "rerolled sparks"
    fakes.answers = [None, None, spark_reroll.EMOJI["original"]]
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check(len(fakes.posts) == 1 and len(fakes.posts[0][1]) == 2,
          "one question, with both sets as pictures")
    check("Power" in fakes.posts[0][0] and "Turf" in fakes.posts[0][0],
          "the message lists both sets too")
    check("Priority skills bought (2): Groundwork, Superstan" in fakes.posts[0][0],
          "and the priority skills bought, in the list's order, tiers matched exactly")
    config.SKILL_LIST = saved_list
    check(state.spark_choice == "original",
          "the answer is taken once a reaction arrives, however many polls it takes")
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
          and len(entry["original"]) == 19 and len(entry["rerolled"]) == 14,
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
    fakes.answers = [spark_reroll.EMOJI["rerolled"]]
    run(spark_reroll.handle_spark_selection, state, fake_wait)
    check(len(fakes.posts) == 1 and state.spark_choice == "rerolled",
          "with both read, the question is asked and answered")
  finally:
    fakes.restore()
    config.INDEPENDENT_SPARK_REROLL, config.INDEPENDENT_DEBUG_STOP_BEFORE_SPARK_REROLL = saved


# --- the Discord client -------------------------------------------------------------------

class Reply:
  def __init__(self, body):
    self.body = body

  def read(self):
    return self.body

  def __enter__(self):
    return self

  def __exit__(self, *_):
    return False


def discord_cases():
  print("\nThe Discord client:")
  sent = []

  def opener(request, timeout=None):
    sent.append(request)
    url = request.full_url
    if url.endswith("/users/@me"):
      return Reply(b'{"id": "bot", "username": "Tazuna"}')
    if "/reactions/" in url and request.get_method() == "GET":
      if "1%EF%B8%8F%E2%83%A3" in url:
        return Reply(json.dumps([{"id": "bot"}, {"id": "me"}]).encode())
      return Reply(json.dumps([{"id": "bot"}]).encode())
    if request.get_method() == "POST":
      return Reply(b'{"id": "m1"}')
    return Reply(b"")

  message = discord_choice.post("hello", [("original.png", b"\x89PNGxx")], channel="c",
                                token="t", opener=opener)
  body = sent[-1].data
  check(message == "m1" and b"payload_json" in body and b'name="files[0]"' in body
        and b"\x89PNGxx" in body and sent[-1].get_header("Authorization") == "Bot t",
        "a post carries the text, the picture and the bot's token")
  discord_choice.offer("m1", list(spark_reroll.EMOJI.values()), channel="c", token="t",
                       opener=opener, pause=lambda _: None)
  check([r.get_method() for r in sent[-2:]] == ["PUT", "PUT"]
        and "%E2%83%A3/@me" in sent[-1].full_url, "each answer is put on as a reaction")
  picked = discord_choice.answer("m1", list(spark_reroll.EMOJI.values()), "bot",
                                 channel="c", token="t", opener=opener)
  check(picked == spark_reroll.EMOJI["original"],
        "a person's reaction is the answer; the bot's own reactions are not")

  def both(request, timeout=None):
    return Reply(json.dumps([{"id": "bot"}, {"id": "me"}]).encode())
  check(discord_choice.answer("m1", list(spark_reroll.EMOJI.values()), "bot", channel="c",
                              token="t", opener=both) is None,
        "two answers at once are not an answer yet")

  calls = {"n": 0}

  def limited(request, timeout=None):
    calls["n"] += 1
    if calls["n"] == 1:
      raise urllib.error.HTTPError(request.full_url, 429, "slow down", {},
                                   io.BytesIO(b'{"retry_after": 0.01}'))
    return Reply(b'{"id": "bot"}')
  check(discord_choice.whoami(token="t", opener=limited) == {"id": "bot"},
        "told to slow down, it waits and asks again")

  def refused(request, timeout=None):
    raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b"{}"))
  ok, detail = discord_choice.test("bad", "c", opener=refused)
  check(not ok and "token" in detail, f"a bad token is reported in words: {detail!r}")

  print("\nDirect messages:")
  opened = []

  def dm(request, timeout=None):
    if request.full_url.endswith("/users/@me/channels"):
      opened.append(json.loads(request.data))
      return Reply(b'{"id": "dm-channel"}')
    sent.append(request)
    if request.full_url.endswith("/users/@me"):
      return Reply(b'{"id": "bot", "username": "Tazuna"}')
    return Reply(b'{"id": "m2"}')
  saved = {name: getattr(config, name, None) for name in
           ("WEBHOOK_BOT_TOKEN", "WEBHOOK_CHOICE_TARGET", "WEBHOOK_CHOICE_USER_ID",
            "WEBHOOK_CHOICE_CHANNEL_ID")}
  discord_choice._dm_channels.clear()
  try:
    config.WEBHOOK_BOT_TOKEN, config.WEBHOOK_CHOICE_CHANNEL_ID = "t", ""
    config.WEBHOOK_CHOICE_TARGET, config.WEBHOOK_CHOICE_USER_ID = "dm", ""
    check(not discord_choice.configured(), "DMs with no user ID set is not set up")
    config.WEBHOOK_CHOICE_USER_ID = "42"
    check(discord_choice.configured(), "a token and a user ID are enough for DMs")
    discord_choice.post("hi", opener=dm)
    discord_choice.post("again", opener=dm)
    check(opened == [{"recipient_id": "42"}] and sent[-1].full_url.endswith(
        "/channels/dm-channel/messages"),
          "the DM with that user is opened once and posted in, like a channel")
    ok, detail = discord_choice.test("t2", user="7", opener=dm)
    check(ok and "DMs" in detail and opened[-1] == {"recipient_id": "7"},
          "the test goes to the user's DMs when a user is given")
  finally:
    for name, value in saved.items():
      setattr(config, name, value)
    discord_choice._dm_channels.clear()

  def closed(request, timeout=None):
    if request.full_url.endswith("/users/@me"):
      return Reply(b'{"id": "bot", "username": "Tazuna"}')
    raise urllib.error.HTTPError(request.full_url, 403, "no", {}, io.BytesIO(
        b'{"message": "Cannot send messages to this user", "code": 50007}'))
  ok, detail = discord_choice.test("t", user="9", opener=closed)
  check(not ok and "Add to My Apps" in detail,
        f"a user the bot cannot message is told why: {detail!r}")

  print("\nNotifications, when DMs are set up:")
  import utils.notifications as notifications
  import utils.webhook as webhook

  class Recorder:
    def __init__(self):
      self.items = []

    def put(self, item):
      self.items.append(item)

  names = ("WEBHOOK_URL", "WEBHOOK_BOT_TOKEN", "WEBHOOK_CHOICE_TARGET",
           "WEBHOOK_CHOICE_USER_ID", "WEBHOOK_CAREER_SUMMARY_ENABLED")
  saved = {name: getattr(config, name, None) for name in names}
  real_queue = webhook._delivery_queue
  queued = Recorder()
  webhook._delivery_queue = queued
  try:
    config.WEBHOOK_URL = "https://discord.com/api/webhooks/1/x"
    config.WEBHOOK_BOT_TOKEN, config.WEBHOOK_CHOICE_USER_ID = "t", "42"
    config.WEBHOOK_CHOICE_TARGET = "channel"
    config.WEBHOOK_CAREER_SUMMARY_ENABLED = True
    webhook.send_started()
    check(queued.items and queued.items[-1][0] == config.WEBHOOK_URL,
          "with the question going to a channel, notifications use the webhook as before")
    config.WEBHOOK_CHOICE_TARGET = "dm"
    webhook.send_started()
    check(queued.items[-1][0] is webhook._DM,
          "with DMs set up, they go to the DMs instead of the webhook")
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

  delivered = []

  def dm_embeds(request, timeout=None):
    if request.full_url.endswith("/users/@me/channels"):
      return Reply(b'{"id": "dm-channel"}')
    delivered.append((request.full_url, json.loads(request.data)))
    return Reply(b'{"id": "m3"}')
  saved = {name: getattr(config, name, None) for name in names}
  discord_choice._dm_channels.clear()
  try:
    config.WEBHOOK_BOT_TOKEN, config.WEBHOOK_CHOICE_USER_ID = "t", "42"
    config.WEBHOOK_CHOICE_TARGET = "dm"
    discord_choice.send_embeds([{"title": "Career 1 Complete"}], opener=dm_embeds)
    check(delivered and delivered[0][0].endswith("/channels/dm-channel/messages")
          and delivered[0][1] == {"embeds": [{"title": "Career 1 Complete"}]},
          "a notification is posted in the DM as the bot, embed and all")
  finally:
    for name, value in saved.items():
      setattr(config, name, value)
    discord_choice._dm_channels.clear()


def main():
  reader_cases()
  handler_cases()
  discord_cases()
  print()
  if failures:
    print(f"{len(failures)} failure(s).")
    return 1
  print("The sparks are read, rerolled when wanted, and kept as answered.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
