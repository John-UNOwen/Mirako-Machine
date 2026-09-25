"""Asking a person a question in Discord and reading the answer back.

A webhook can only post: nothing it sends can be answered. So this talks to Discord as a
bot, with a bot token, over the plain REST API -- no gateway connection, no library. A
question is a message with one reaction per answer already on it; the answer is
whichever of those reactions a person (anyone but the bot itself) has added, read by
polling. Buttons would need the gateway's open connection to be delivered at all, and a
reaction is the same single tap on a phone.

Used for the spark choice after a reroll: both sets are posted as pictures, and the
reaction says which to keep. There is deliberately no timeout here -- the caller decides
how long to wait.
"""

import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import core.config as config
from utils.log import debug, warning

API = "https://discord.com/api/v10"
TIMEOUT = 15
USER_AGENT = "DiscordBot (https://github.com/John-UNOwen/Mirako-Machine, 1.0)"
# Discord allows adding reactions only a few times a second; this keeps well inside it.
REACTION_SPACING = 0.6


class DiscordError(Exception):
  """A request Discord refused, with its reason in words a person can act on."""


def configured():
  """Whether a bot token and a channel are both set."""
  return bool(_token() and _channel())


def _token():
  return str(getattr(config, "WEBHOOK_BOT_TOKEN", "") or "").strip()


def _channel():
  return str(getattr(config, "WEBHOOK_CHOICE_CHANNEL_ID", "") or "").strip()


def _request(method, path, body=None, content_type="application/json", token=None,
             opener=urllib.request.urlopen):
  """One API call, retried once after a rate limit. Returns the parsed reply or None."""
  for attempt in range(3):
    request = urllib.request.Request(f"{API}{path}", data=body, method=method)
    request.add_header("Authorization", f"Bot {token or _token()}")
    request.add_header("User-Agent", USER_AGENT)
    if body is not None:
      request.add_header("Content-Type", content_type)
    try:
      with opener(request, timeout=TIMEOUT) as reply:
        raw = reply.read()
        return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
      detail = error.read().decode("utf-8", "replace")[:300]
      if error.code == 429 and attempt < 2:
        try:
          wait = float(json.loads(detail).get("retry_after", 1.0))
        except ValueError:
          wait = 1.0
        time.sleep(min(wait, 10.0))
        continue
      raise DiscordError(_explain(error.code, detail)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
      raise DiscordError(f"Could not reach Discord: {error}") from None
  raise DiscordError("Discord kept asking to slow down.")


def _explain(code, detail):
  if code == 401:
    return "Discord rejected the bot token. Copy it again from the Developer Portal's Bot page."
  if code == 403:
    return ("The bot cannot use that channel. Invite it to the server, and give it View "
            "Channel, Send Messages, Attach Files, Add Reactions and Read Message History there.")
  if code == 404:
    return "No channel with that ID. Right-click the channel and Copy Channel ID."
  return f"Discord answered {code}: {detail}"


def _multipart(fields, files):
  """A multipart/form-data body: `fields` {name: str}, `files` [(field, filename, bytes)]."""
  boundary = uuid.uuid4().hex
  body = io.BytesIO()
  for name, value in fields.items():
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n"
               f"Content-Type: application/json\r\n\r\n".encode())
    body.write(value.encode("utf-8"))
    body.write(b"\r\n")
  for field, filename, data in files:
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
               f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n".encode())
    body.write(data)
    body.write(b"\r\n")
  body.write(f"--{boundary}--\r\n".encode())
  return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def whoami(token=None, opener=urllib.request.urlopen):
  """The bot's own user, which proves the token works. {"id", "username"}."""
  return _request("GET", "/users/@me", token=token, opener=opener)


def post(text, images=(), channel=None, token=None, opener=urllib.request.urlopen):
  """Post `text` with PNG `images` [(filename, bytes)]. Returns the message id."""
  channel = channel or _channel()
  payload = {"content": text, "attachments": [
      {"id": index, "filename": name} for index, (name, _) in enumerate(images)]}
  body, content_type = _multipart(
      {"payload_json": json.dumps(payload)},
      [(f"files[{index}]", name, data) for index, (name, data) in enumerate(images)])
  message = _request("POST", f"/channels/{channel}/messages", body, content_type,
                     token=token, opener=opener)
  return message["id"]


def edit(message_id, text, channel=None, token=None, opener=urllib.request.urlopen):
  """Replace a posted message's text, keeping its pictures."""
  channel = channel or _channel()
  _request("PATCH", f"/channels/{channel}/messages/{message_id}",
           json.dumps({"content": text}).encode("utf-8"), token=token, opener=opener)


def _emoji_path(emoji):
  return urllib.parse.quote(emoji, safe="")


def offer(message_id, emojis, channel=None, token=None, opener=urllib.request.urlopen,
          pause=time.sleep):
  """Put one reaction per answer on the message, so answering is a tap on it."""
  channel = channel or _channel()
  for emoji in emojis:
    _request("PUT", f"/channels/{channel}/messages/{message_id}/reactions/"
                    f"{_emoji_path(emoji)}/@me", token=token, opener=opener)
    pause(REACTION_SPACING)


def answer(message_id, emojis, bot_id, channel=None, token=None,
           opener=urllib.request.urlopen):
  """The one emoji of `emojis` a person has reacted with, or None.

  None as well when people have picked more than one: that is not an answer yet, and
  whoever did it can take one away.
  """
  channel = channel or _channel()
  picked = []
  for emoji in emojis:
    users = _request("GET", f"/channels/{channel}/messages/{message_id}/reactions/"
                            f"{_emoji_path(emoji)}?limit=100", token=token,
                     opener=opener) or []
    if any(str(user.get("id")) != str(bot_id) for user in users):
      picked.append(emoji)
  if len(picked) == 1:
    return picked[0]
  if len(picked) > 1:
    debug(f"More than one answer reacted ({picked}); waiting for just one.")
  return None


def test(token, channel, opener=urllib.request.urlopen):
  """Check a token and channel from the Setup page: (ok, what to tell the person)."""
  try:
    me = whoami(token=token, opener=opener)
    post(f"✅ {me.get('username', 'The bot')} can post spark choices in this channel.",
         channel=channel, token=token, opener=opener)
  except DiscordError as error:
    warning(f"Discord bot test failed: {error}")
    return False, str(error)
  except (KeyError, TypeError, ValueError) as error:
    return False, f"Discord sent something unexpected: {error}"
  return True, f"Connected as {me.get('username')}; a test message was posted."
