"""ntfy-bimp entry point.

Subscribes to an ntfy topic and routes commands to the beets importer.

Commands (sent via ntfy):
  START   — begin an import of the configured import_path
  A       — apply the best candidate match
  M       — switch to track-by-track mode
  S       — skip this item
  U       — import as-is (no metadata change)
"""

import logging
import threading
import traceback

import beets
import beets.library
import beets.ui

from .bridge import NtfyBridge, NtfyImportSession
from .config import load_config
from .ntfy_client import publish, subscribe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Import runner (background thread)
# ---------------------------------------------------------------------------


def run_import(bridge: NtfyBridge, lib: beets.library.Library, cfg: dict) -> None:
    """Run a beets import session inside bridge.session_lock."""
    import_path = cfg.get("import_path", "")
    with bridge.session_lock:
        try:
            publish(f"Starting import of {import_path} …", config=cfg)
            session = NtfyImportSession(
                bridge=bridge,
                lib=lib,
                loghandler=logging.StreamHandler(),
                paths=[import_path.encode()],
                query=None,
            )
            session.run()
            publish("Import complete.", config=cfg)
        except Exception as exc:
            log.error("Import error: %s\n%s", exc, traceback.format_exc())
            publish(f"Import error: {exc}", config=cfg)
            raise


# ---------------------------------------------------------------------------
# Message dispatcher
# ---------------------------------------------------------------------------


def handle_message(
    cmd: str,
    bridge: NtfyBridge,
    lib: beets.library.Library,
    cfg: dict,
) -> None:
    """Dispatch an incoming ntfy command."""
    if cmd == "START":
        acquired = bridge.session_lock.acquire(blocking=False)
        if not acquired:
            publish("Import already in progress.", config=cfg)
            return
        # Release immediately — run_import will re-acquire inside the thread.
        bridge.session_lock.release()

        t = threading.Thread(
            target=run_import, args=(bridge, lib, cfg), daemon=True, name="importer"
        )
        t.start()

    elif cmd in ("A", "M", "S", "U"):
        bridge.set_response(cmd)

    else:
        publish(f"Unknown command: {cmd!r}. Valid: START A M S U", config=cfg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    cfg = load_config()
    log.info("Config loaded. ntfy topic: %s/%s", cfg["ntfy_url"], cfg["topic"])

    # Initialise beets from its own config file (reads ~/.config/beets/config.yaml
    # or wherever BEETSDIR points).
    beets.config.read()

    lib = beets.ui._open_library(beets.config)
    log.info("Opened beets library: %s", lib.path)

    bridge = NtfyBridge(config=cfg)

    publish("ntfy-bimp online. Send START to begin an import.", config=cfg)
    log.info("Subscribing to ntfy stream …")

    for event in subscribe(cfg):
        raw = event.get("message", "").strip()
        if "server" in event.get("tags", []):
            log.debug("Ignoring own message: %r", raw)
            continue
        cmd = raw.upper()
        log.info("Received: %r → command %r", raw, cmd)
        handle_message(cmd, bridge, lib, cfg)


if __name__ == "__main__":
    main()
