"""Asking the player something and hearing back.

The spark choice needs a question the player answers from their phone, and the
notifications need somewhere to land. Both go through here rather than to the relay
directly, so the way the question travels can change without the reroll or the
notifications knowing.

Today there is one way: SharedBot, the Mirako bot everyone links to, run behind a relay
the owner hosts (core/relay_client.py), answered with a button by DM. Its contract is the
relay project's API.md (Mirako-Relay, beside this repository).

A question is text, pictures and a few options. An answer is the id of the option picked.
"""

import time
import uuid

from core import relay_client


class Option:
  """One answer: `id` is what comes back, `label` and `emoji` what its button shows."""
  __slots__ = ("id", "label", "emoji")

  def __init__(self, id, label, emoji):
    self.id = id
    self.label = label
    self.emoji = emoji


class AskError(Exception):
  """The question could not be asked or read, in words a person can act on."""


class AskGone(AskError):
  """The question can never be answered (expired, or the link was removed): ask again."""


class SharedBot:
  """The shared bot behind the relay. The question is a DM with one button per option."""
  name = "shared_bot"
  # The relay allows one status poll per question every 10 seconds.
  POLL_SECONDS = 10
  TEXT_LIMIT = 1900

  _last_poll = {}

  def configured(self):
    return bool(relay_client.token())

  def ask(self, text, images, options, key=None):
    """Send the question; returns an id to read the answer with.

    `key` is the relay's Idempotency-Key: the caller passes the same one on each retry
    of one question, so a retry after a lost reply gets that question back rather than a
    second DM, and a new one for each new question. The relay hands back the question a
    key first made for a day, whatever is asked with it after, so a key must never
    outlive its question -- which is why the caller holds it, per question, and not this
    object, which outlives careers. Without one, every call is a new question.
    """
    key = key or uuid.uuid4().hex
    if len(text) > self.TEXT_LIMIT:
      text = text[:self.TEXT_LIMIT - 1] + "…"
    try:
      question_id = relay_client.ask(
          text, images, [{"id": o.id, "label": o.label, "emoji": o.emoji} for o in options],
          key)
    except relay_client.RelayError as error:
      raise AskError(str(error)) from None
    except (KeyError, TypeError) as error:
      raise AskError(f"The relay sent something unexpected: {error}") from None
    return question_id

  def answer(self, question_id, options, clock=time.monotonic):
    now = clock()
    last = SharedBot._last_poll.get(question_id)
    if last is not None and now - last < self.POLL_SECONDS:
      return None
    SharedBot._last_poll[question_id] = now
    try:
      reply = relay_client.status(question_id) or {}
    except relay_client.RelayError as error:
      if error.status in (401, 404):
        raise AskGone(str(error)) from None
      raise AskError(str(error)) from None
    if reply.get("status") == "expired":
      raise AskGone("The question expired before it was answered.")
    if reply.get("status") != "answered":
      return None
    return next((option.id for option in options if option.id == reply.get("answer")), None)

  def finish(self, question_id, text):
    SharedBot._last_poll.pop(question_id, None)
    try:
      relay_client.settle(question_id, text[:self.TEXT_LIMIT])
    except relay_client.RelayError:
      pass

  def notify(self, embeds):
    relay_client.notify(embeds)


def backend():
  """The way questions travel: the shared Mirako bot."""
  return SharedBot()
