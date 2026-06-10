import logging
import asyncio
import time
from pathlib import Path
from typing import Callable, Optional, Tuple, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage as ha_storage
from homeassistant.util import json as ha_json
from homeassistant.util import slugify as ha_slugify

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
        """Atomically mutate the cached data and schedule a save.

        Unlike load/modify/save in callers, the mutation happens on the live
        cache under the lock, so concurrent updates from different sensors
        cannot overwrite each other.
        """
        await self.async_load()  # Ensure the cache is populated
        async with self._lock:
            assert self._cache is not None
            try:
                mutator(self._cache)
            except Exception as e:
                _LOGGER.error("Storage mutation failed: %s", e)
                raise
            if self._save_task is None or self._save_task.done():
                self._save_task = asyncio.create_task(self._debounced_save())
            return dict(self._cache)

    async def async_set_key(self, key: str, value) -> None:
        """Atomically set a single top-level key and schedule a save."""
        def _mutate(data: dict) -> None:
            data[key] = value
        await self.async_update(_mutate)

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


def _slugify_fragment(value: str) -> str:
	"""Slugify strings consistently using underscores."""
	slug = ha_slugify(value or "")
	if not slug:
		return ""
	return slug.replace("-", "_").strip("_")


def derive_constant_base_name(device_conf: dict) -> str:
	"""Determine deterministic base name for a constant power device."""
	switch_entity = (device_conf or {}).get("switch_entity_id", "")
	entity_core = switch_entity.split(".", 1)[1] if "." in switch_entity else switch_entity
	slug_core = _slugify_fragment(entity_core)
	if not slug_core:
		# Fallback to friendly name if provided
		name = (device_conf or {}).get("name") or "constant_load"
		slug_core = _slugify_fragment(name)
	if not slug_core:
		slug_core = "constant_load"
	return f"{slug_core}_constant"


def format_constant_power_devices_text(devices: List[dict]) -> str:
	"""Render stored constant devices as editable text."""
	lines: List[str] = []
	for device in devices or []:
		switch_entity = device.get("switch_entity_id")
		power_w = device.get("power_w")
		if not switch_entity or power_w is None:
			continue
		name = device.get("name", "").strip()
		value = f"{power_w:.3f}".rstrip("0").rstrip(".")
		line = f"{switch_entity} = {value} W"
		if name:
			line = f"{line} | {name}"
		lines.append(line)
	return "\n".join(lines)


def parse_constant_power_devices_text(raw_value: str) -> Tuple[List[dict], List[str]]:
	"""Parse multiline text input into constant device definitions.

	Each line format: switch.entity = 3000 W | Optional Friendly Name
	Units default to Watts; suffix 'kW' (case-insensitive) is also accepted.
	"""
	if not raw_value:
		return [], []
	devices: List[dict] = []
	errors: List[str] = []
	for idx, line in enumerate(raw_value.splitlines(), start=1):
		original_line = line
		line = line.strip()
		if not line or line.startswith("#"):
			continue
		name_part = None
		if "|" in line:
			line, name_part = [part.strip() for part in line.split("|", 1)]
		if "=" not in line:
			errors.append(f"Line {idx}: Missing '=' in '{original_line}'")
			continue
		entity_id, power_str = [part.strip() for part in line.split("=", 1)]
		if not entity_id:
			errors.append(f"Line {idx}: Missing switch entity ID in '{original_line}'")
			continue
		if not entity_id.startswith("switch."):
			errors.append(f"Line {idx}: '{entity_id}' must start with 'switch.'")
			continue
		power_clean = power_str.lower().replace(" ", "")
		multiplier = 1.0
		if power_clean.endswith("kw"):
			multiplier = 1000.0
			power_clean = power_clean[:-2]
		elif power_clean.endswith("w"):
			power_clean = power_clean[:-1]
		if not power_clean:
			errors.append(f"Line {idx}: Missing numeric value in '{original_line}'")
			continue
		try:
			numeric_value = float(power_clean)
		except ValueError:
			errors.append(f"Line {idx}: '{power_str}' is not a valid number")
			continue
		power_w = numeric_value * multiplier
		if power_w <= 0:
			errors.append(f"Line {idx}: Power must be positive in '{original_line}'")
			continue
		devices.append({
			"switch_entity_id": entity_id,
			"power_w": power_w,
			"name": name_part or None
		})
	return devices, errors