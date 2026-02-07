import logging
import asyncio
import time
from pathlib import Path
from typing import Callable, Optional, Tuple, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage as ha_storage
from homeassistant.util import json as ha_json
from homeassistant.util import slugify as ha_slugify

from .const import (
	DOMAIN,
	STORAGE_FILE,
	CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID,
	CONF_CONSTANT_DEVICE_POWER_W,
	CONF_CONSTANT_DEVICE_NAME,
	CONF_CONSTANT_DEVICE_INSTANCES,
)

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


def _slugify_fragment(value: str) -> str:
	"""Slugify strings consistently using underscores."""
	slug = ha_slugify(value or "")
	if not slug:
		return ""
	return slug.replace("-", "_").strip("_")


def derive_constant_base_name(device_conf: dict, instance: int | None = None) -> str:
	"""Determine deterministic base name for a constant power device.

	If instance is provided, it will be suffixed to keep names unique when one switch is
	split into multiple entities.
	"""
	switch_entity = (device_conf or {}).get(CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID, "")
	entity_core = switch_entity.split(".", 1)[1] if "." in switch_entity else switch_entity
	slug_core = _slugify_fragment(entity_core)
	if not slug_core:
		# Fallback to friendly name if provided
		name = (device_conf or {}).get(CONF_CONSTANT_DEVICE_NAME) or "constant_load"
		slug_core = _slugify_fragment(name)
	if not slug_core:
		slug_core = "constant_load"
	base_name = f"{slug_core}_constant"
	if instance is None:
		return base_name
	try:
		instance_int = int(instance)
	except (TypeError, ValueError):
		return base_name
	if instance_int < 1:
		return base_name
	return f"{base_name}_{instance_int}"


def _normalise_positive_int(value, default: int = 1, min_value: int = 1, max_value: int = 50) -> int:
	try:
		number = int(value)
	except (TypeError, ValueError):
		return default
	if number < min_value:
		return min_value
	if number > max_value:
		return max_value
	return number


def expand_constant_power_devices(devices: List[dict]) -> List[tuple[str, dict]]:
	"""Expand stored constant device configs into per-entity configs.

	- Uses CONF_CONSTANT_DEVICE_POWER_W as TOTAL power for the switch.
	- If CONF_CONSTANT_DEVICE_INSTANCES > 1, power is split evenly across instances.
	- Returned dicts include per-instance power in CONF_CONSTANT_DEVICE_POWER_W.
	"""
	expanded: List[tuple[str, dict]] = []
	for device in devices or []:
		switch_entity = (device or {}).get(CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID)
		if not switch_entity:
			continue

		instances = _normalise_positive_int(
			(device or {}).get(CONF_CONSTANT_DEVICE_INSTANCES, 1),
			default=1,
			min_value=1,
			max_value=50,
		)

		try:
			total_power_w = float((device or {}).get(CONF_CONSTANT_DEVICE_POWER_W, 0))
		except (TypeError, ValueError):
			total_power_w = 0.0
		if total_power_w <= 0:
			continue

		per_power_w = total_power_w / float(instances) if instances > 0 else total_power_w
		base_name_source = dict(device)
		base_name_source[CONF_CONSTANT_DEVICE_INSTANCES] = instances
		base_name_source[CONF_CONSTANT_DEVICE_POWER_W] = total_power_w

		for idx in range(1, instances + 1):
			base_name = derive_constant_base_name(
				base_name_source,
				idx if instances > 1 else None,
			)
			cfg = dict(device)
			cfg[CONF_CONSTANT_DEVICE_INSTANCES] = instances
			cfg["instance"] = idx
			cfg["total_power_w"] = total_power_w
			cfg[CONF_CONSTANT_DEVICE_POWER_W] = per_power_w
			name = (cfg.get(CONF_CONSTANT_DEVICE_NAME) or "").strip()
			if name and instances > 1:
				cfg[CONF_CONSTANT_DEVICE_NAME] = f"{name} {idx}"
			expanded.append((base_name, cfg))
	return expanded


def format_constant_power_devices_text(devices: List[dict]) -> str:
	"""Render stored constant devices as editable text."""
	lines: List[str] = []
	for device in devices or []:
		switch_entity = device.get(CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID)
		power_w = device.get(CONF_CONSTANT_DEVICE_POWER_W)
		if not switch_entity or power_w is None:
			continue
		name = str(device.get(CONF_CONSTANT_DEVICE_NAME, "") or "").strip()
		instances = _normalise_positive_int(
			device.get(CONF_CONSTANT_DEVICE_INSTANCES, 1),
			default=1,
			min_value=1,
			max_value=50,
		)
		try:
			total_power_w = float(power_w)
		except (TypeError, ValueError):
			continue
		value = f"{total_power_w:.3f}".rstrip("0").rstrip(".")
		line = f"{switch_entity} = {value} W"
		if instances > 1:
			each = total_power_w / float(instances)
			each_value = f"{each:.3f}".rstrip("0").rstrip(".")
			line = f"{line} x{instances} ({each_value} W each)"
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