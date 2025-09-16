import logging
import asyncio
import time
from pathlib import Path
from typing import Callable, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage as ha_storage
from homeassistant.util import json as ha_json

from .const import DOMAIN, STORAGE_FILE

_LOGGER = logging.getLogger(__name__)


class StorageManager:
    """Centralised, debounced JSON storage using Home Assistant Store.

    - Persists data under .storage/<key> using HA's Store API (atomic, safe).
    - Debounces and rate-limits saves to prevent FD exhaustion.
    - Caches data in-memory for fast read/modify/write cycles.
    - Migrates once from legacy flat file (<config>/<STORAGE_FILE>) if present.
    """

    def __init__(self, hass: HomeAssistant, key: str = "energy_sensor_generator", version: int = 1,
                 debounce_seconds: float = 2.0, min_interval_seconds: float = 60.0) -> None:
        self._hass = hass
        self._store = ha_storage.Store(hass, version=version, key=key)
        self._lock: asyncio.Lock = asyncio.Lock()
        self._cache: Optional[dict] = None
        self._load_task: Optional[asyncio.Task] = None
        self._save_task: Optional[asyncio.Task] = None
        self._last_save_ts: float = 0.0
        self._last_err_ts: float = 0.0
        self._debounce_seconds = debounce_seconds
        self._min_interval_seconds = min_interval_seconds
        self._migrated: bool = True  # Migration removed

    async def async_load(self) -> dict:
        """Load data once and cache it; returns a shallow copy for safety."""
        async with self._lock:
            if self._cache is not None:
                return dict(self._cache)
            # Single-flight load: reuse in-flight task if present
            if self._load_task is None or self._load_task.done():
                self._load_task = asyncio.create_task(self._load_from_store())
            task = self._load_task

        try:
            data = await task
        except Exception as e:
            _LOGGER.warning("Storage load task failed: %s", e)
            data = {}

        async with self._lock:
            self._cache = data or {}
            self._load_task = None
            return dict(self._cache)

    async def _load_from_store(self) -> dict:
        try:
            data = await self._store.async_load()
            return data or {}
        except Exception as e:
            _LOGGER.warning("Storage load failed via Store: %s", e)
            return {}

    async def async_save(self, data: dict) -> None:
        """Set cache and schedule a debounced, rate-limited save."""
        async with self._lock:
            self._cache = dict(data)
            if self._save_task is None or self._save_task.done():
                self._save_task = asyncio.create_task(self._debounced_save())

    async def async_update(self, mutator: Callable[[dict], None]) -> dict:
        """Load, mutate, and schedule save; returns updated data."""
        data = await self.async_load()
        try:
            mutator(data)
        except Exception as e:
            _LOGGER.error("Storage mutation failed: %s", e)
            raise
        await self.async_save(data)
        return data

    async def _debounced_save(self) -> None:
        try:
            await asyncio.sleep(self._debounce_seconds)

            # Respect minimum interval between writes
            now = time.time()
            remaining = self._min_interval_seconds - (now - self._last_save_ts)
            if remaining > 0:
                await asyncio.sleep(remaining)

            async with self._lock:
                to_save = dict(self._cache or {})

            try:
                await self._store.async_save(to_save)
                self._last_save_ts = time.time()
            except Exception as e:
                # Rate-limit error logging to once per 30s
                now2 = time.time()
                if now2 - self._last_err_ts > 30:
                    _LOGGER.error("Failed to save storage: %s", e)
                    self._last_err_ts = now2
        except Exception:
            # Swallow exceptions to avoid task storms
            pass

    async def async_flush(self) -> None:
        """Flush any pending save by awaiting the in-flight task."""
        task = None
        async with self._lock:
            if self._save_task and not self._save_task.done():
                task = self._save_task
        if task:
            try:
                await task
            except Exception:
                pass