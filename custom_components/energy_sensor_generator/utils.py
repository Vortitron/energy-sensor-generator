import json
import logging
from pathlib import Path
import aiofiles

_LOGGER = logging.getLogger(__name__)

async def load_storage(storage_path: str) -> dict:
    """Load persistent storage."""
    try:
        async with aiofiles.open(storage_path, "r") as f:
            content = await f.read()
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

async def save_storage(storage_path: str, data: dict) -> None:
    """Save persistent storage."""
    try:
        async with aiofiles.open(storage_path, "w") as f:
            await f.write(json.dumps(data, indent=2))
    except IOError as e:
        _LOGGER.error(f"Failed to save storage: {e}") 