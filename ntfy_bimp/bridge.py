"""NtfyBridge and NtfyImportSession.

The bridge coordinates between the beets import pipeline (which runs in its
own thread) and the ntfy subscriber loop (main thread) by using a threading
Event to block the pipeline until the user replies.
"""

import logging
import threading
from typing import Optional

import beets.importer as bimporter
from beets.autotag.match import Recommendation

from .ntfy_client import publish

log = logging.getLogger(__name__)

_REC_LABELS = {
    Recommendation.strong: "STRONG",
    Recommendation.medium: "MEDIUM",
    Recommendation.low: "LOW",
    Recommendation.none: "NONE",
}

_FOOTER = "\nReply: A=apply best  M=more  S=skip  U=use-as-is"


class NtfyBridge:
    """Thread-safe bridge between the ntfy subscriber loop and the beets
    import pipeline."""

    def __init__(self, config: dict):
        self.config = config
        self.response_event = threading.Event()
        self.session_lock = threading.Lock()
        self.user_choice: Optional[str] = None

    def set_response(self, choice: str) -> None:
        """Called from the subscriber loop when the user sends a reply."""
        self.user_choice = choice
        self.response_event.set()

    # ------------------------------------------------------------------
    # Task formatting helpers
    # ------------------------------------------------------------------

    def _format_candidates(self, candidates, top_n=3) -> str:
        lines = []
        for i, cand in enumerate(candidates[:top_n], start=1):
            info = cand.info
            dist = float(cand.distance)
            match_pct = f"{(1 - dist):.0%}"
            if hasattr(info, "album"):
                # AlbumMatch
                year = getattr(info, "year", None) or "?"
                label = f"{i}. {info.artist} – {info.album} ({year}) [{match_pct} match]"
            else:
                # TrackMatch
                artist = getattr(info, "artist", None) or "?"
                label = f"{i}. {artist} – {info.title} [{match_pct} match]"
            lines.append(label)
        return "\n".join(lines)

    def format_album_task(self, task) -> str:
        info_lines = []

        # Track count
        track_count = len(task.items) if task.items else "?"
        info_lines.append(f"Album import — {track_count} track(s)")

        # Common artist / album name from task
        if task.cur_artist or task.cur_album:
            info_lines.append(f"Detected: {task.cur_artist} / {task.cur_album}")

        # Candidates
        if task.candidates:
            rec_label = _REC_LABELS.get(task.rec, "?")
            info_lines.append(f"Recommendation: {rec_label}")
            info_lines.append("")
            info_lines.append("Candidates:")
            info_lines.append(self._format_candidates(task.candidates))
        else:
            info_lines.append("No candidates found.")

        info_lines.append(_FOOTER)
        return self._truncate("\n".join(info_lines))

    def format_singleton_task(self, task) -> str:
        info_lines = []
        info_lines.append("Singleton import")

        item = task.item if hasattr(task, "item") else None
        if item:
            info_lines.append(
                f"File: {item.artist or '?'} – {item.title or '?'}"
            )

        if task.candidates:
            rec_label = _REC_LABELS.get(task.rec, "?")
            info_lines.append(f"Recommendation: {rec_label}")
            info_lines.append("")
            info_lines.append("Candidates:")
            info_lines.append(self._format_candidates(task.candidates))
        else:
            info_lines.append("No candidates found.")

        info_lines.append(_FOOTER)
        return self._truncate("\n".join(info_lines))

    def _truncate(self, text: str) -> str:
        limit = self.config.get("char_limit", 4000)
        if len(text) > limit:
            return text[: limit - 3] + "..."
        return text

    # ------------------------------------------------------------------
    # Blocking decision wait
    # ------------------------------------------------------------------

    def wait_for_choice(self, task, *, is_album: bool):
        """Format task, publish to ntfy, block until user responds.

        Returns a value suitable for task.set_choice():
          - An AlbumMatch / TrackMatch object  → beets applies it
          - Action.SKIP, Action.ASIS           → skip / import as-is
          - Action.TRACKS                      → switch to track mode (M)
        """
        if is_album:
            message = self.format_album_task(task)
            title = "Beets: album match needed"
        else:
            message = self.format_singleton_task(task)
            title = "Beets: track match needed"

        publish(message, title=title, config=self.config)

        # Block until the subscriber calls set_response()
        self.response_event.clear()
        self.response_event.wait()  # no timeout — must respond

        choice = self.user_choice
        log.info("User chose: %s", choice)

        action = bimporter.Action

        if choice == "A":
            if task.candidates:
                return task.candidates[0]
            else:
                publish("No candidates — skipping.", config=self.config)
                return action.SKIP
        elif choice == "M":
            return action.TRACKS
        elif choice == "S":
            return action.SKIP
        elif choice == "U":
            return action.ASIS
        else:
            publish(f"Unknown choice '{choice}' — skipping.", config=self.config)
            return action.SKIP


# ---------------------------------------------------------------------------
# Custom ImportSession
# ---------------------------------------------------------------------------


class NtfyImportSession(bimporter.ImportSession):
    """ImportSession subclass that routes decisions through NtfyBridge."""

    def __init__(self, bridge: NtfyBridge, lib, loghandler, paths, query):
        super().__init__(lib, loghandler, paths, query)
        self.bridge = bridge

    def should_resume(self, path):
        # Never resume interactively; always start fresh.
        return False

    def choose_match(self, task):
        return self.bridge.wait_for_choice(task, is_album=True)

    def choose_item(self, task):
        return self.bridge.wait_for_choice(task, is_album=False)

    def resolve_duplicate(self, task, found_duplicates):
        """Called when a duplicate is found and config has no default action.

        We notify the user and skip the duplicate import.
        """
        count = len(found_duplicates)
        publish(
            f"Duplicate found ({count} existing match(es)) — skipping.",
            config=self.bridge.config,
        )
        task.set_choice(bimporter.Action.SKIP)
