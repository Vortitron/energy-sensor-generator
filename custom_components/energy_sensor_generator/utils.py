import json
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

def load_storage(storage_path: str) -> dict:
    """Load persistent storage."""
    try:
        with open(storage_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_storage(storage_path: str, data: dict) -> None:
    """Save persistent storage."""
    try:
        with open(storage_path, "w") as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        _LOGGER.error(f"Failed to save storage: {e}") 