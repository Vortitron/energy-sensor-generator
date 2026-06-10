"""Shared helpers for logging, entity naming and storage persistence.

These are used by both the main energy sensors and the period sensors so they
live in their own module to avoid circular imports.
"""
import hashlib
import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, CONF_DEBUG_LOGGING

_LOGGER = logging.getLogger(__name__)

DEBUG_LOG_THROTTLE_SECONDS = 30


def is_debug_enabled(hass: HomeAssistant) -> bool:
	"""Check if debug logging is enabled for this integration."""
	if DOMAIN not in hass.data:
		return False
	for config_entry in hass.config_entries.async_entries(DOMAIN):
		if config_entry.options.get(CONF_DEBUG_LOGGING, False):
			return True
	return False


def debug_log(hass: HomeAssistant, message: str) -> None:
	"""Log debug message only if debug logging is enabled, throttled per message group."""
	if not is_debug_enabled(hass):
		return
	if DOMAIN not in hass.data:
		return
	current_time = time.time()
	log_throttle_key = "_debug_log_throttle"
	throttle = hass.data[DOMAIN].setdefault(log_throttle_key, {})
	# Group similar messages by a hash of their first 50 characters
	message_hash = hashlib.md5(message[:50].encode()).hexdigest()[:8]
	last_log_time = throttle.get(message_hash, 0)
	if current_time - last_log_time > DEBUG_LOG_THROTTLE_SECONDS:
		_LOGGER.warning(f"DEBUG: {message}")
		throttle[message_hash] = current_time


def info_log(hass: HomeAssistant, message: str, force: bool = False) -> None:
	"""Log info message, respecting debug setting unless forced."""
	if force or is_debug_enabled(hass):
		_LOGGER.info(message)


def get_friendly_name(hass: HomeAssistant, entity_id: str) -> str:
	"""Get the friendly name for an entity, falling back to derived name from entity ID."""
	entity_registry = er.async_get(hass)
	entity_entry = entity_registry.async_get(entity_id)

	def _strip_power_suffix(name: str) -> str:
		if name.lower().endswith(" power") or name.lower().endswith("_power"):
			return name[:-6]
		return name

	# Friendly name from state attributes is what users see in the UI
	state = hass.states.get(entity_id)
	if state and state.attributes.get("friendly_name"):
		return _strip_power_suffix(state.attributes["friendly_name"])

	# Custom name from entity registry
	if entity_entry and entity_entry.name:
		return _strip_power_suffix(entity_entry.name)

	# Device name if entity is part of a device
	if entity_entry and entity_entry.device_id:
		device_registry = dr.async_get(hass)
		device = device_registry.async_get(entity_entry.device_id)
		if device and device.name_by_user:
			return device.name_by_user
		elif device and device.name:
			return device.name

	# Fall back to deriving from entity ID, e.g. "sensor.smart_plug_2_power" -> "Smart Plug 2"
	base_name = entity_id.replace("sensor.", "").replace("_power", "")
	return base_name.replace("_", " ").title()


def get_friendly_name_from_base(hass: HomeAssistant, base_name: str) -> str:
	"""Get friendly name by trying different possible power sensor patterns."""
	# Handle disambiguated base names (e.g. "smart_plug_energy_2" from "sensor.smart_plug_power_2")
	possible_sensors = []

	if "_energy_" in base_name:
		parts = base_name.split("_energy_")
		if len(parts) == 2:
			possible_sensors.append(f"sensor.{parts[0]}_power_{parts[1]}")

	possible_sensors.extend([
		f"sensor.{base_name}_power",
		f"sensor.{base_name}",
		f"{base_name}_power",
		f"{base_name}"
	])

	for sensor_id in possible_sensors:
		if hass.states.get(sensor_id):
			return get_friendly_name(hass, sensor_id)

	# If no sensor found, just clean up the base_name
	return base_name.replace("_", " ").replace("_energy_", " ").title()


def get_unique_entity_name(hass: HomeAssistant, proposed_name: str, domain: str = "sensor") -> str:
	"""Generate a unique entity name by checking for conflicts and adding suffixes if needed."""
	entity_registry = er.async_get(hass)

	base_name = proposed_name
	counter = 1

	while True:
		name_exists = False
		conflicting_entity = None
		is_own_entity = False

		for entity_id, entry in entity_registry.entities.items():
			if entity_id.startswith(f"{domain}."):
				_curr_name = (entry.name or entry.original_name or "").lower()
				if _curr_name == proposed_name.lower():
					if entry.platform == DOMAIN:
						# It's our own entity, don't treat as conflict
						is_own_entity = True
						_LOGGER.debug(f"Detected own entity with name '{proposed_name}': {entity_id}")
					else:
						name_exists = True
						conflicting_entity = entity_id
					break

		# Also check current states for entities that might not be in registry yet
		if not name_exists and not is_own_entity:
			for state in hass.states.async_all():
				if (state.entity_id.startswith(f"{domain}.") and
					state.attributes.get("friendly_name", "").lower() == proposed_name.lower()):
					name_exists = True
					conflicting_entity = state.entity_id
					break

		if not name_exists:
			if counter > 1:
				_LOGGER.info(f"Entity name conflict resolved: using '{proposed_name}' instead of '{base_name}'")
			return proposed_name

		if counter == 2:  # Log only on first conflict detection
			_LOGGER.warning(f"Entity name conflict detected: '{base_name}' already exists (conflicting entity: {conflicting_entity}). Adding suffix.")

		counter += 1
		proposed_name = f"{base_name} ({counter})"


async def persist_storage_key(hass: HomeAssistant, storage_manager, storage_key: str, payload: dict, sensor_name: str) -> None:
	"""Persist one sensor's payload via the shared storage manager (atomic) or a fallback Store."""
	try:
		if storage_manager:
			await storage_manager.async_set_key(storage_key, payload)
		else:
			from homeassistant.helpers import storage as ha_storage
			store = ha_storage.Store(hass, version=1, key="energy_sensor_generator")
			storage = await store.async_load() or {}
			storage[storage_key] = payload
			await store.async_save(storage)
	except Exception as e:
		_LOGGER.warning(f"Failed to save state for {sensor_name}: {e}")
