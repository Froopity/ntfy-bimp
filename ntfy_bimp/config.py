import os
import yaml


DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/ntfy-bimp/config.yaml")
REQUIRED_KEYS = ("ntfy_url", "topic")
DEFAULTS = {
    "char_limit": 4000,
}


def load_config(path=None):
    """Load ntfy-bimp YAML config.

    Args:
        path: Path to config file. Defaults to ~/.config/ntfy-bimp/config.yaml.

    Returns:
        dict with config values.

    Raises:
        FileNotFoundError: If config file does not exist.
        ValueError: If required keys are missing.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH

    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise ValueError(
            f"Missing required config keys: {', '.join(missing)} "
            f"(config file: {path})"
        )

    result = dict(DEFAULTS)
    result.update(cfg)

    # Normalise: strip trailing slash from ntfy_url
    result["ntfy_url"] = result["ntfy_url"].rstrip("/")

    # Treat blank token as absent
    if not result.get("token"):
        result.pop("token", None)

    return result
