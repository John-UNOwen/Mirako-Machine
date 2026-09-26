import json
import queue
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from enum import Enum

import core.bot as bot
import core.config as config
from utils.log import info

_TIMEOUT = 5
_USERNAME = "Mirako Machine"
_FOOTER = "Mirako Machine"

_COLOR_SUCCESS = 0x2ECC71
_COLOR_ERROR = 0xE74C3C
_COLOR_WARNING = 0xF1C40F
_COLOR_INFO = 0x3498DB


class StopReason(str, Enum):
    FINISHED = "finished"
    STUCK = "stuck"
    CLAW_MACHINE = "claw machine"
    UNKNOWN = "unknown"


# Titles without the instance marker, which _titled adds when the message is sent. They
# are held apart because this dict is built once at import, before anything has declared
# an identity -- with the marker baked in, every instance's stop notification claimed to
# be instance 1 for the life of the process, whichever emulator it was driving. The
# positional identity was the known flaw; this was the marker failing to be even that.
_STOP_STYLES = {
    StopReason.FINISHED: (_COLOR_SUCCESS, "🎉 Training Finished!"),
    StopReason.STUCK: (_COLOR_ERROR, "🚨 Mirako Machine Got Stuck"),
    StopReason.CLAW_MACHINE: (_COLOR_WARNING, "🕹️ Claw Machine - Manual Play Required"),
    StopReason.UNKNOWN: (_COLOR_ERROR, "⚠️ Mirako Machine Stopped"),
}


def _titled(text):
    """`text` with this instance's name on it, read now rather than at import."""
    return f"{text} - (Instance {bot.instance_label()})"


_delivery_queue: queue.Queue = queue.Queue()
# Queued in place of a URL for a message bound for the user's DMs.
_DM = object()


def _delivery_worker():
    while True:
        url, payload = _delivery_queue.get()
        try:
            if url is _DM:
                # To the player through core.asker -- the Mirako bot's DMs -- which replaces
                # the webhook once it is set up. The webhook's own display name is left
                # off: a bot posts as itself.
                from core import asker
                asker.backend().notify(json.loads(payload)["embeds"])
                continue
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", "UmaAuto/1.0")
            with urllib.request.urlopen(req, timeout=_TIMEOUT):
                pass
        except Exception as exc:
            info(f"Webhook delivery failed: {exc}")
        finally:
            _delivery_queue.task_done()


threading.Thread(target=_delivery_worker, daemon=True, name="webhook-delivery").start()


def _url():
    return getattr(config, "WEBHOOK_URL", "").strip()


def _config_name():
    return getattr(config, "CONFIG_NAME", "unknown")


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _field(name, value, inline=True):
    return {"name": name, "value": value, "inline": inline}


def _embed(title, color, fields, footer=None):
    return {
        "title": title,
        "color": color,
        "fields": fields + [_field("Config", f"`{_config_name()}`")],
        "timestamp": _timestamp(),
        "footer": {"text": footer or _FOOTER},
    }


def _post(embed):
    """Queue one notification: to the user's DMs when those are set up, else the webhook.

    DMs win. Someone who has linked the Mirako bot wants its messages in one place, and the webhook is the older, channel-bound route to
    the same person.
    """
    from core import asker
    payload = json.dumps({"username": _USERNAME, "embeds": [embed]}).encode("utf-8")
    if asker.backend().configured():
        _delivery_queue.put((_DM, payload))
        return
    url = _url()
    if not url:
        return
    _delivery_queue.put((url, payload))


def _looks_like_discord_webhook(url: str) -> bool:
    """Whether `url` is a Discord webhook endpoint.

    Checked because the test button posts a URL straight from a text field. Without a
    guard it would be a button that makes the machine issue an arbitrary POST to
    anywhere, which is a much bigger thing than "check my webhook works".
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (host == "discord.com" or host.endswith(".discord.com")
             or host == "discordapp.com" or host.endswith(".discordapp.com"))
        and "/api/webhooks/" in parsed.path
    )


def send_test(url: str = ""):
    """Post a test message and wait for the result. Returns (ok, detail).

    Sent synchronously, unlike everything else here. The queue exists so a slow or dead
    webhook cannot hold up the bot -- but a test button that queues would report success
    the moment the message was handed off, which is exactly the thing being tested.

    `url` is the field's current contents, so the button works before the config is
    saved; falling back to the saved one keeps it useful with the field left alone.
    """
    url = (url or _url()).strip()
    if not url:
        return False, "No webhook URL set."
    if not _looks_like_discord_webhook(url):
        return False, ("That does not look like a Discord webhook URL. It should start "
                       "https://discord.com/api/webhooks/")

    embed = _embed(
        title=_titled("✅ Test Message"),
        color=_COLOR_INFO,
        fields=[_field("Status", "Notifications are working. This was sent by the "
                                 "Test button.", inline=False)],
    )
    payload = json.dumps({"username": _USERNAME, "embeds": [embed]}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "UmaAuto/1.0")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return True, f"Sent. Discord accepted it ({response.status})."
    except urllib.error.HTTPError as exception:
        if exception.code == 404:
            return False, "Discord says that webhook does not exist (404). Check the URL."
        if exception.code == 401:
            return False, "Discord rejected the token (401). Copy the URL again."
        return False, f"Discord refused it ({exception.code})."
    except TimeoutError:
        # Raised instead of URLError when the connection opens but nothing comes back,
        # which is what a firewall swallowing the request looks like from here.
        return False, f"Discord did not respond within {_TIMEOUT} seconds."
    except urllib.error.URLError as exception:
        return False, f"Could not reach Discord: {exception.reason}"
    except Exception as exception:  # noqa: BLE001 - a test button must never raise
        return False, f"Failed to send: {exception}"


def send_started():
    _post(_embed(title=_titled("🥕 Mirako Machine Started"), color=_COLOR_INFO, fields=[]))


def send_stopped(reason: StopReason):
    color, title = _STOP_STYLES.get(reason, _STOP_STYLES[StopReason.UNKNOWN])
    _post(_embed(title=_titled(title), color=color, fields=[]))


def send_career_complete(record: dict, careers_done: int):
    """One career's result, from the Training Log summary the stats already collect.

    The point of a notification for a mode that runs unattended for hours: it says the
    thing finished and how it went, without needing the machine looked at. Fields that
    could not be read are shown as "?" rather than omitted, so a gap is visible as a gap.
    """
    def value(key, fmt="{:,}"):
        raw = record.get(key)
        return fmt.format(raw) if isinstance(raw, (int, float)) else "?"

    duration = record.get("duration_seconds")
    took = f"{duration // 60}m" if isinstance(duration, int) else "?"
    races, wins = value("races", "{}"), value("wins", "{}")

    _post(
        _embed(
            title=_titled(f"🏁 Career {careers_done} Complete"),
            color=_COLOR_SUCCESS,
            fields=[
                _field("Rating", value("rating")),
                _field("Fans", value("fans")),
                _field("Record", f"{wins}/{races}"),
                _field("Took", took),
                _field("Speed", value("speed", "{}")),
                _field("Stamina", value("stamina", "{}")),
                _field("Power", value("power", "{}")),
                _field("Guts", value("guts", "{}")),
                _field("Wit", value("wit", "{}")),
                _field("Skill Pts", value("skill_points", "{}")),
            ],
        )
    )


def send_recovering(what: str, attempt: int):
    """Something interrupted the run and the bot is climbing back.

    Sent rather than stayed silent about because the bot recovers on its own: the
    interesting signal is that it is happening at all, and repeatedly, not that anything
    needs doing right now. Warning-coloured for the same reason -- this is not a stop.
    """
    _post(
        _embed(
            title=_titled("🔌 Recovering"),
            color=_COLOR_WARNING,
            fields=[
                _field("What happened", what, inline=False),
                _field("Attempt", str(attempt)),
            ],
        )
    )


def send_careers_paused(why: str, carrying_on: str):
    """Careers cannot start until the player does something; the bot keeps going.

    Sent whatever the notification switches say: unlike a recovery, nothing clears this
    on its own, and a bot quietly doing only its daily tasks looks the same as one that
    is working.
    """
    _post(
        _embed(
            title=_titled("⏸️ Careers Paused"),
            color=_COLOR_WARNING,
            fields=[
                _field("Why", why, inline=False),
                _field("Now", carrying_on, inline=False),
            ],
        )
    )


def send_skills_bought(skills: list[str]):
    skill_list = "\n".join(f"- {s}" for s in skills)
    _post(
        _embed(
            title=_titled("🎓 Skills Purchased"),
            color=_COLOR_SUCCESS,
            fields=[_field("Skills", skill_list, inline=False)],
        )
    )
