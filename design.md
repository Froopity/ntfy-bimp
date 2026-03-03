# Design Document: Beets-ntfy Remote Importer

This document outlines the design for a Python-based coordinator that bridges the **beets** media library manager and the **ntfy** notification service. This allows for fully interactive, remote music imports without requiring SSH or direct terminal access.

---

## 1. System Architecture

The system operates as a multi-threaded Python application. One thread listens for incoming ntfy commands, while another manages the Beets `ImportSession`. Communication between the two is handled via a synchronized state object and threading events.

### Core Components

* **ntfy Subscriber:** Long-polls the ntfy API for user commands ("start", "A", "M", etc.).
* **Beets Importer Thread:** Executes the `beets.importer` logic.
* **The Bridge (Decider):** A custom function passed to Beets that intercepts "manual choice" moments, sends data to ntfy, and blocks until a response is received.

---

## 2. Interaction Logic

### Command Mapping

| User Input (via ntfy) | Beets Action Mapping |
| --- | --- |
| `start` | Initializes `ImportSession` and begins processing. |
| `A` | `beets.importer.Action.APPLY` (Accept match) |
| `M` | `beets.importer.Action.MANUAL` (Look for more matches) |
| `S` | `beets.importer.Action.SKIP` |
| `U` | `beets.importer.Action.USE_AS_IS` |

---

## 3. Implementation Details

### A. The Custom Decider (The Bridge)

This function replaces the standard terminal prompt. It uses a `threading.Event` to pause Beets' execution while waiting for the user to respond via ntfy.

```python
import threading
from beets import importer

class NtfyBridge:
    def __init__(self):
        self.response_event = threading.Event()
        self.user_choice = None

    def decider_hook(self, session, items, candidates, recommendation):
        # 1. Format match data and send to ntfy
        summary = self.format_choices(items, candidates)
        send_to_ntfy(summary)

        # 2. Wait for the subscriber thread to signal an update
        self.response_event.clear()
        self.response_event.wait()

        # 3. Convert input string to Beets Action enum
        return self.map_input_to_action(self.user_choice)

    def format_choices(self, items, candidates):
        # Logic to turn Beets Match objects into readable text
        return f"Found {len(candidates)} matches. Reply with A, M, or S."

```

### B. Running the Import Session

Using Beets as a library requires setting up a `GlobalConfig` and an `ImportSession`.

```python
from beets.library import Library
from beets.importer import ImportSession

def run_beets_task(paths, bridge):
    lib = Library('/path/to/music.db')
    session = ImportSession(
        lib,
        loghandler=None,
        paths=paths,
        query=None
    )
    # Inject our custom decider
    session.decider = bridge.decider_hook
    session.run()
    send_to_ntfy("Import Complete.")

```

### C. The ntfy Listener

This loop runs in the background, updating the `NtfyBridge` state whenever a message arrives.

```python
import requests
import json

def listen_to_ntfy(bridge):
    url = "https://ntfy.sh/your_beets_topic/json"
    with requests.get(url, stream=True) as r:
        for line in r.iter_lines():
            if line:
                data = json.loads(line)
                msg = data.get("message", "").strip().upper()
                
                if msg == "START":
                    threading.Thread(target=run_beets_task, args=([...], bridge)).start()
                else:
                    bridge.user_choice = msg
                    bridge.response_event.set()

```

---

## 4. Key Constraints & Requirements

* **Concurrency:** Only one import session can be active at a time to prevent database locks.
* **Persistence:** The script should run as a `systemd` service to ensure it restarts on server reboot.
* **Security:** Use a private ntfy topic or `Authorization` headers to prevent unauthorized users from triggering imports.
* **Formatting:** ntfy has a character limit; the `format_choices` logic must truncate extremely long match lists to avoid delivery failure.
