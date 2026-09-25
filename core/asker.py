"""Asking the player something and hearing back, whichever way that is set up.

The spark choice needs a question the player answers from their phone, and the
notifications need somewhere to land. Both used to talk to core/discord_choice.py -- the
player's own Discord bot -- directly. They talk to this instead, so the way the question
travels can change without the reroll or the notifications knowing:

  * OwnBot: the player's own Discord bot, by DM, answered with a reaction. Works today.
  * SharedBot: one bot everyone links to, run behind a relay the owner hosts, so no
    player needs a bot of their own. Not built yet; its contract is the relay project's
    API.md (Mirako-Relay, beside this repository).

A question is text, pictures and a few options. An answer is the id of the option picked.
"""

from core import discord_choice


class Option:
  """One answer: `id` is what comes back, `label` what a button would say, `emoji` what a
  reaction uses where there are no buttons."""
  __slots__ = ("id", "label", "emoji")

  def __init__(self, id, label, emoji):
    self.id = id
    self.label = label
    self.emoji = emoji


class AskError(Exception):
  """The question could not be asked or read, in words a person can act on."""


class OwnBot:
  """The player's own bot. The question is a DM; the options are reactions on it."""
  name = "own_bot"

  def configured(self):
    return discord_choice.configured()

  def ask(self, text, images, options):
    """Send the question; returns an id to read the answer with."""
    try:
      bot_id = discord_choice.whoami()["id"]
      how = ", ".join(f"{option.emoji} {option.label.lower()}" for option in options)
      message_id = discord_choice.post(f"{text}\n\nReact to answer: {how}.", images)
    except (discord_choice.DiscordError, KeyError, TypeError) as error:
      raise AskError(str(error)) from None
    try:
      discord_choice.offer(message_id, [option.emoji for option in options])
    except discord_choice.DiscordError:
      # Without the bot's own reactions the player can still add one by hand, and that
      # is read the same way; not worth failing the question over.
      pass
    return f"{message_id}:{bot_id}"

  def answer(self, question_id, options):
    """The id of the option picked, or None while there is no answer yet."""
    message_id, bot_id = question_id.split(":", 1)
    try:
      picked = discord_choice.answer(message_id, [option.emoji for option in options],
                                     bot_id)
    except discord_choice.DiscordError as error:
      raise AskError(str(error)) from None
    return next((option.id for option in options if option.emoji == picked), None)

  def finish(self, question_id, text):
    """Replace the question's text once it is answered, so it reads as settled."""
    try:
      discord_choice.edit(question_id.split(":", 1)[0], text)
    except discord_choice.DiscordError:
      pass

  def notify(self, embeds):
    discord_choice.send_embeds(embeds)


class SharedBot:
  """The shared bot behind the relay. Not built yet: never configured, so never used."""
  name = "shared_bot"

  def configured(self):
    return False

  def ask(self, text, images, options):
    raise AskError("The shared bot is not available yet.")

  def answer(self, question_id, options):
    raise AskError("The shared bot is not available yet.")

  def finish(self, question_id, text):
    pass

  def notify(self, embeds):
    raise AskError("The shared bot is not available yet.")


def backend():
  """The way questions travel. Only the player's own bot exists so far."""
  return OwnBot()
