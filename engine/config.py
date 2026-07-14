"""Engine configuration.

Reads `engine.config.json` from the aggregator repo root (gitignored;
copy `engine.config.example.json` to create it). The config points at a
PRIVATE config repo ("signaldesk_dir") that holds the source registry and
receives the generated digests — the engine itself carries zero personal data.
"""

import json
import os
from datetime import timedelta, timezone

# India Standard Time as a fixed offset. We deliberately avoid zoneinfo:
# Windows has no system tz database and stdlib-only means no `tzdata` package.
IST = timezone(timedelta(hours=5, minutes=30))

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_PATH = os.path.join(_REPO_ROOT, "engine.config.json")


class ConfigError(Exception):
    pass


class Config:
    """Loaded engine configuration with convenient accessors."""

    def __init__(self, data: dict, path: str):
        self._data = data
        self.path = path
        self.signaldesk_dir = data.get("signaldesk_dir", "")
        self.db_path = data.get("db_path", "")
        self.window_hours_first_run = int(data.get("window_hours_first_run", 48))
        self.beat_weights = data.get("beat_weights", {})
        self.hf_keywords = [k.lower() for k in data.get("hf_keywords", [])]
        self.beat_keywords = data.get("beat_keywords", {})
        self.default_beat_weight = float(data.get("default_beat_weight", 0.5))
        self.hn_points_norm = float(data.get("hn_points_norm", 200.0))

    # --- derived paths inside the private config repo -----------------------
    @property
    def registry_path(self) -> str:
        return os.path.join(self.signaldesk_dir, "registry", "sources.json")

    @property
    def digests_dir(self) -> str:
        return os.path.join(self.signaldesk_dir, "digests")

    @property
    def pitches_dir(self) -> str:
        return os.path.join(self.signaldesk_dir, "pitches")

    def validate(self):
        if not self.signaldesk_dir or not os.path.isdir(self.signaldesk_dir):
            raise ConfigError(
                f"signaldesk_dir does not exist: {self.signaldesk_dir!r}. "
                f"Edit {self.path}."
            )
        if not self.db_path:
            raise ConfigError(f"db_path is empty in {self.path}.")
        if not os.path.isfile(self.registry_path):
            raise ConfigError(f"registry not found: {self.registry_path}")


def load_config(path: str = None) -> Config:
    path = path or os.environ.get("ENGINE_CONFIG") or _DEFAULT_CONFIG_PATH
    if not os.path.isfile(path):
        raise ConfigError(
            f"engine config not found at {path}. "
            f"Copy engine.config.example.json to engine.config.json and edit it."
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Config(data, path)
