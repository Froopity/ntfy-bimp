import json
import logging
import time

import requests

log = logging.getLogger(__name__)

_BACKOFF_BASE = 2
_BACKOFF_MAX = 60


def _auth_headers(config):
    token = config.get("token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def publish(message, title=None, priority=None, *, config):
    """Publish a message to the ntfy topic.

    Args:
        message: The notification body.
        title: Optional notification title.
        priority: Optional ntfy priority string (e.g. "high").
        config: Config dict from load_config().
    """
    char_limit = config.get("char_limit", 4000)
    if len(message) > char_limit:
        message = message[: char_limit - 3] + "..."

    url = f"{config['ntfy_url']}/{config['topic']}"
    headers = _auth_headers(config)
    headers["Content-Type"] = "text/plain"
    if title:
        headers["Title"] = title
    if priority:
        headers["Priority"] = priority

    resp = requests.post(url, data=message.encode(), headers=headers, timeout=15)
    resp.raise_for_status()


def subscribe(config):
    """Generator that yields messages from the ntfy topic.

    Reconnects automatically with exponential backoff on connection errors.

    Yields:
        dicts with at least 'message' and 'id' keys (raw ntfy event objects).
    """
    url = f"{config['ntfy_url']}/{config['topic']}/json"
    headers = _auth_headers(config)
    backoff = _BACKOFF_BASE

    while True:
        try:
            log.info("Connecting to ntfy stream: %s", url)
            with requests.get(
                url, headers=headers, stream=True, timeout=None
            ) as resp:
                resp.raise_for_status()
                backoff = _BACKOFF_BASE  # reset on successful connection
                for line in resp.iter_lines():
                    if not line:
                        # keepalive empty line
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("Could not parse ntfy line: %r", line)
                        continue
                    # ntfy sends "open" and "keepalive" event types; only yield messages
                    if event.get("event") == "message":
                        yield event
        except requests.RequestException as exc:
            log.warning(
                "ntfy connection lost (%s). Reconnecting in %ds...", exc, backoff
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
