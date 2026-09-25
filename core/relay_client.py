"""Talking to Mirako Relay: the shared Discord bot the owner runs, so a player needs no bot
of their own.

The relay holds the bot's token; this side holds only a link token, got once by pasting
the code `/link` gives in the bot's DMs. The contract is API.md in the Mirako-Relay
repository beside this one. Plain urllib, like core/discord_choice.py.
"""

import io
import json
import os
import urllib.error
import urllib.request
import uuid

import core.config as config

# The owner's relay. MIRAKO_RELAY_URL points a test or a moved relay elsewhere.
DEFAULT_URL = "https://mirako.0006767.xyz"
TIMEOUT = 20
USER_AGENT = "MirakoMachine (https://github.com/John-UNOwen/Mirako-Machine)"
# Statuses the relay says will not change however often the call is repeated.
PERMANENT = {400, 401, 404, 409, 413}


class RelayError(Exception):
  """A call the relay refused or could not take, in words a player can act on.

  `status` is the HTTP status (None when the relay was not reached), `code` the relay's
  own error code, and `permanent` whether repeating the call can help."""

  def __init__(self, message, status=None, code=None):
    super().__init__(message)
    self.status = status
    self.code = code
    self.permanent = status in PERMANENT


def base_url():
  return (os.environ.get("MIRAKO_RELAY_URL") or DEFAULT_URL).rstrip("/")


def token():
  return str(getattr(config, "WEBHOOK_RELAY_TOKEN", "") or "").strip()


def client_name():
  try:
    with io.open("version.txt", encoding="utf-8") as handle:
      return f"Mirako Machine {handle.read().strip()}"
  except OSError:
    return "Mirako Machine"


def _request(method, path, body=None, content_type="application/json", auth=None,
             headers=None, opener=urllib.request.urlopen):
  """One call. Returns the parsed reply, or None for an empty one."""
  request = urllib.request.Request(f"{base_url()}{path}", data=body, method=method)
  request.add_header("User-Agent", USER_AGENT)
  if auth is not False:
    request.add_header("Authorization", f"Bearer {auth or token()}")
  if body is not None:
    request.add_header("Content-Type", content_type)
  for name, value in (headers or {}).items():
    request.add_header(name, value)
  try:
    with opener(request, timeout=TIMEOUT) as reply:
      raw = reply.read()
      return json.loads(raw) if raw else None
  except urllib.error.HTTPError as error:
    raw = error.read().decode("utf-8", "replace")
    try:
      reply = json.loads(raw)
    except ValueError:
      reply = {}
    message = reply.get("message") if isinstance(reply, dict) else None
    code = reply.get("error") if isinstance(reply, dict) else None
    if not message:
      message = f"The relay answered {error.code}."
    raise RelayError(message, error.code, code) from None
  except (urllib.error.URLError, TimeoutError, OSError) as error:
    raise RelayError(f"Could not reach the relay: {error}") from None


def link(code, opener=urllib.request.urlopen):
  """Swap a /link code for a token. {"token", "user": {"id", "name"}}."""
  body = json.dumps({"code": code.strip().upper(), "client": client_name()}).encode("utf-8")
  return _request("POST", "/v1/link", body, auth=False, opener=opener)


def me(auth=None, opener=urllib.request.urlopen):
  return _request("GET", "/v1/me", auth=auth, opener=opener)


def test(auth=None, opener=urllib.request.urlopen):
  _request("POST", "/v1/test", auth=auth, opener=opener)


def ask(text, images, options, key, opener=urllib.request.urlopen):
  """Ask by DM with buttons. `options` [{"id", "label", "emoji"}]. Returns the question id.

  `key` is the Idempotency-Key: the same key on a retry gets the first question back
  rather than a second one."""
  boundary = uuid.uuid4().hex
  body = io.BytesIO()
  payload = json.dumps({"text": text, "options": options})
  body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
             f"Content-Type: application/json\r\n\r\n".encode())
  body.write(payload.encode("utf-8"))
  body.write(b"\r\n")
  for index, (name, data) in enumerate(images):
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[{index}]\"; "
               f"filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n".encode())
    body.write(data)
    body.write(b"\r\n")
  body.write(f"--{boundary}--\r\n".encode())
  reply = _request("POST", "/v1/questions", body.getvalue(),
                   f"multipart/form-data; boundary={boundary}",
                   headers={"Idempotency-Key": key}, opener=opener)
  return reply["id"]


def status(question_id, opener=urllib.request.urlopen):
  """{"status": "pending" | "answered" | "expired", "answer"?: option id}."""
  return _request("GET", f"/v1/questions/{question_id}", opener=opener)


def settle(question_id, text, opener=urllib.request.urlopen):
  _request("PATCH", f"/v1/questions/{question_id}",
           json.dumps({"text": text}).encode("utf-8"), opener=opener)


def notify(embeds, opener=urllib.request.urlopen):
  _request("POST", "/v1/notifications", json.dumps({"embeds": embeds}).encode("utf-8"),
           opener=opener)
