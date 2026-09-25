import core.config as config
import utils.webhook as webhook
from utils.webhook import StopReason

_stop_sent = False


def _webhook_enabled():
    """Whether notifications go anywhere: a webhook, or the user's DMs, which replace it."""
    from core import asker
    return bool(getattr(config, "WEBHOOK_URL", "").strip()) or asker.backend().configured()


def on_started():
    global _stop_sent
    _stop_sent = False
    if not _webhook_enabled():
        return
    webhook.send_started()


def on_stopped(reason: StopReason):
    global _stop_sent
    if _stop_sent:
        return
    _stop_sent = True
    if not _webhook_enabled():
        return
    webhook.send_stopped(reason)


def reset_notification_state():
    """Per-run notification state, cleared when a run starts.

    Was `reset_progress_tracking`, and also cleared a year marker for `on_progress` --
    a career-progress webhook whose milestones are turn-by-turn years this build does
    not have, and which nothing had called for a long time. Removed 2026-09-20 along
    with its config key `webhook.progress_enabled`, which reached `reload_config` and
    none of the other four places a key needs.
    """
    global _stop_sent
    _stop_sent = False


def on_career_complete(record: dict, careers_done: int):
    if not _webhook_enabled():
        return
    if not getattr(config, "WEBHOOK_CAREER_SUMMARY_ENABLED", True):
        return
    webhook.send_career_complete(record, careers_done)


def on_recovering(what: str, attempt: int):
    if not _webhook_enabled():
        return
    if not getattr(config, "WEBHOOK_RECOVERY_ENABLED", True):
        return
    webhook.send_recovering(what, attempt)


def on_careers_paused(why: str, carrying_on: str):
    if not _webhook_enabled():
        return
    webhook.send_careers_paused(why, carrying_on)


def on_skills_bought(skills: list[str]):
    if not _webhook_enabled():
        return
    # Its own flag. It used to share one with a turn-by-turn stat snapshot that this
    # build never sent; that event and its key are gone, and this switch is the only one
    # left here that is actually wired to something.
    if not getattr(config, "WEBHOOK_SKILLS_ENABLED", True):
        return
    webhook.send_skills_bought(skills)
