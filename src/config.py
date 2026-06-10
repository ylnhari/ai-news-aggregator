"""Configuration management for AI News Aggregator."""

import os
import json
from pathlib import Path
from typing import Dict, List, Any
import yaml
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Configuration management for the application."""

    def __init__(self):
        """Initialize configuration from environment and config files."""
        self.root_dir = Path(__file__).parent.parent
        self.config_dir = self.root_dir / "config"
        self.data_dir = self.root_dir / "data"
        self.reports_dir = self.root_dir / "reports"

        # Ensure directories exist
        self.reports_dir.mkdir(exist_ok=True)
        self.data_dir.mkdir(exist_ok=True)

        # Load settings from YAML
        self.settings = self._load_yaml_config()

        # Load Twitter accounts
        self.twitter_accounts = self._load_twitter_accounts()

    def _load_yaml_config(self) -> Dict[str, Any]:
        """Load settings from settings.yaml."""
        settings_path = self.config_dir / "settings.yaml"
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _load_twitter_accounts(self) -> List[Dict[str, str]]:
        """Load Twitter accounts from config."""
        accounts_path = self.config_dir / "twitter_accounts.json"
        accounts_list = []

        if accounts_path.exists():
            with open(accounts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Flatten the nested account structure
                for category, accounts in data.get("accounts", {}).items():
                    accounts_list.extend(accounts)

        return accounts_list

    @property
    def gemini_api_key(self) -> str:
        """Get Gemini API key from environment."""
        return os.getenv("GEMINI_API_KEY", "")

    @property
    def days_back(self) -> int:
        """Get number of days to look back."""
        return int(os.getenv("DAYS_BACK", self.settings.get("twitter", {}).get("days_back", 1)))

    @property
    def output_dir(self) -> str:
        """Get output directory."""
        return os.getenv("OUTPUT_DIR", str(self.reports_dir))

    def get_setting(self, *keys: str, default: Any = None) -> Any:
        """Get a setting from the configuration."""
        current = self.settings
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return default
        return current if current is not None else default

    def validate(self) -> List[str]:
        """Return a list of human-readable configuration problems (empty = OK)."""
        problems = []
        if not self.gemini_api_key:
            problems.append(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your "
                "free key from https://aistudio.google.com/"
            )
        elif self.gemini_api_key == "your_gemini_api_key_here":
            problems.append("GEMINI_API_KEY still has the placeholder value from .env.example.")
        feeds = self.get_setting("sources", "rss", "feeds", default=[])
        if self.get_setting("sources", "rss", "enabled", default=True) and not feeds:
            problems.append("RSS is enabled but sources.rss.feeds is empty in settings.yaml.")
        return problems
