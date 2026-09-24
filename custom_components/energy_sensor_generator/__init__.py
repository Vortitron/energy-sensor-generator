"""Energy Sensor Generator: kWh sensors built from power sensors."""
import json
import logging
from datetime import datetime
from pathlib import Path

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
	CONF_CONSTANT_POWER_DEVICES,
	CONF_CREATE_SYNTHETIC_GRID_TOTAL,
	CONF_PRICE_ADJUST_SENSORS,
	DOMAIN,
)
from .entity_helpers import debug_log as _debug_log
from .hourly_copy_service import copy_from_previous_hour_service
from .naming import desired_unique_ids, enabled_periods, plan_sources
from .utils import StorageManager, derive_constant_base_name

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

POWER_UNITS = {"w", "watt", "watts", "kw", "kilowatt", "kilowatts"}
ENERGY_UNITS = {"wh", "kwh", "mwh"}
# Only trusted for sensors without a unit; "_usage" alone also matches
# data, water and CPU usage sensors.
POWER_NAME_PATTERNS = ("_power", "_consumption", "_usage", "power_", "watt")


def detect_power_sensors(hass: HomeAssistant) -> list:
	"""Return entity_ids of sensors that look like power sensors (W / kW)."""
	entity_registry = er.async_get(hass)
	power_sensors = []

	for state in hass.states.async_all("sensor"):
		entity_id = state.entity_id
		unit = (state.attributes.get("unit_of_measurement") or "").strip()
		unit_lower = unit.lower()
		device_class = state.attributes.get("device_class") or ""
		reason = None

		if unit_lower in POWER_UNITS:
			reason = f"unit '{unit}'"
		elif device_class == "power":
			reason = "device_class 'power'"
		else:
			entity_reg = entity_registry.async_get(entity_id)
			if entity_reg and (entity_reg.unit_of_measurement in ("W", "kW") or entity_reg.device_class == "power"):
				reason = "registry"
			elif not unit and any(pattern in entity_id for pattern in POWER_NAME_PATTERNS):
				try:
					float(state.state)
					reason = "name pattern"
				except (TypeError, ValueError):
					pass

		if reason:
			power_sensors.append(entity_id)
			_debug_log(hass, f"Detected power sensor: {entity_id} ({reason}, state: {state.state})")
		elif unit_lower in ENERGY_UNITS or device_class == "energy":
			_debug_log(hass, f"Skipped energy sensor {entity_id}: energy sensors cannot be used as power sources")

	_LOGGER.debug("Detected %d power sensors", len(power_sensors))
	return power_sensors


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
	"""Register services once for the integration."""
	hass.data.setdefault(DOMAIN, {})
	_async_register_services(hass)
	return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Set up Energy Sensor Generator from a config entry."""
	hass.data.setdefault(DOMAIN, {})
	storage_manager = StorageManager(hass)

	# Main integration device; sw_version comes from the manifest so it never goes stale
	from homeassistant.loader import async_get_integration
	integration = await async_get_integration(hass, DOMAIN)
	dr.async_get(hass).async_get_or_create(
		config_entry_id=entry.entry_id,
		identifiers={(DOMAIN, "main")},
		name="Energy Sensor Generator",
		manufacturer="Energy Sensor Generator",
		model="Integration",
		sw_version=str(integration.version),
	)

	hass.data[DOMAIN][entry.entry_id] = {
		"storage_manager": storage_manager,
		"entities": {},
	}

	_async_remove_stale_entities(hass, entry)
	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

	# Options changes (including Save in the Configure dialog) rebuild the entities
	entry.async_on_unload(entry.add_update_listener(_async_options_updated))

	# Pending debounced writes must reach disk before Home Assistant exits
	async def _async_final_write(_event) -> None:
		await storage_manager.async_flush()

	entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_FINAL_WRITE, _async_final_write))
	return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
	await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Unload a config entry."""
	# Entities save their final state while being removed, so flush afterwards
	unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
	if unload_ok:
		entry_data = hass.data[DOMAIN].pop(entry.entry_id)
		await entry_data["storage_manager"].async_flush()
	return unload_ok


async def async_remove_config_entry_device(hass: HomeAssistant, config_entry: ConfigEntry, device_entry) -> bool:
	"""Allow deleting a generated device from the UI.

	Removal is permitted only when the device no longer exposes any entities
	belonging to this integration - e.g. stale devices left over from an old
	naming scheme. Devices that still have generated entities are protected so
	a live sensor cannot be deleted by accident (it would just be recreated).
	"""
	entity_registry = er.async_get(hass)
	device_entities = er.async_entries_for_device(
		entity_registry, device_entry.id, include_disabled_entities=True
	)
	has_live_entity = any(e.config_entry_id == config_entry.entry_id for e in device_entities)
	return not has_live_entity


def _async_remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Remove registry entries the current options no longer produce.

	Stored totals are kept, so re-selecting a sensor later restores its value.
	"""
	options = entry.options
	plans = plan_sources(
		options.get("selected_power_sensors"),
		options.get(CONF_CONSTANT_POWER_DEVICES),
		derive_constant_base_name,
	)
	wanted = desired_unique_ids(
		plans,
		enabled_periods(options),
		options.get(CONF_PRICE_ADJUST_SENSORS),
		bool(options.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False)),
	)
	entity_registry = er.async_get(hass)
	stale = [
		reg_entry.entity_id
		for reg_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
		if reg_entry.unique_id not in wanted
	]
	for entity_id in stale:
		entity_registry.async_remove(entity_id)
	if stale:
		_LOGGER.info("Removed %d entities that are no longer configured: %s", len(stale), ", ".join(stale))


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

def _loaded_entry(hass: HomeAssistant) -> tuple[ConfigEntry, dict]:
	"""Return the loaded config entry and its runtime data."""
	for entry in hass.config_entries.async_entries(DOMAIN):
		entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
		if isinstance(entry_data, dict) and "storage_manager" in entry_data:
			return entry, entry_data
	raise HomeAssistantError("Energy Sensor Generator is not set up")


def _live_entities(hass: HomeAssistant) -> list:
	"""Generated entities that are currently added to Home Assistant."""
	_, entry_data = _loaded_entry(hass)
	return [entity for entity in entry_data.get("entities", {}).values() if entity.hass is not None]


def _adjustable_entities(hass: HomeAssistant) -> list:
	"""Main and period kWh sensors (entities whose total can be changed)."""
	return [entity for entity in _live_entities(hass) if hasattr(entity, "async_set_total")]


def _main_energy_entities(hass: HomeAssistant) -> list:
	return [entity for entity in _live_entities(hass) if hasattr(entity, "async_clear_statistical_anchor")]


def _live_entity_for(hass: HomeAssistant, entity_id: str):
	"""Map an entity_id to its live entity, or raise a user-facing error."""
	reg_entry = er.async_get(hass).async_get(entity_id)
	if not reg_entry or reg_entry.platform != DOMAIN:
		raise ServiceValidationError(f"{entity_id} is not an Energy Sensor Generator sensor")
	_, entry_data = _loaded_entry(hass)
	entity = entry_data.get("entities", {}).get(reg_entry.unique_id)
	if entity is None or entity.hass is None:
		raise ServiceValidationError(f"{entity_id} is not currently loaded")
	return entity


def _notify(hass: HomeAssistant, title: str, message: str, notification_id: str) -> None:
	persistent_notification.async_create(hass, message, title=title, notification_id=notification_id)


def _async_register_services(hass: HomeAssistant) -> None:
	services = {
		"generate_sensors": (generate_sensors_service, vol.Schema({})),
		"reset_energy_sensors": (
			reset_energy_sensors_service,
			vol.Schema({
				vol.Optional("sensors"): vol.Any(cv.string, [cv.string]),
				vol.Optional("selected_sensors"): vol.Any(cv.string, [cv.string]),
				vol.Optional("reset_factor", default=0.5): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
				vol.Optional("reset_to_zero", default=False): cv.boolean,
			}),
		),
		"debug_sensor_detection": (debug_sensor_detection_service, vol.Schema({})),
		"diagnose_sensor": (diagnose_sensor_service, vol.Schema({vol.Required("sensor_name"): cv.string})),
		"migrate_entity_ids": (migrate_entity_ids_service, vol.Schema({})),
		"list_sensors": (list_sensors_service, vol.Schema({})),
		"export_energy_data": (
			export_energy_data_service,
			vol.Schema({vol.Optional("target_path"): cv.string}),
		),
		"import_energy_data": (
			import_energy_data_service,
			vol.Schema({
				vol.Required("source_path"): cv.string,
				vol.Optional("mode", default="merge"): vol.In(["merge", "replace"]),
				vol.Optional("reassign_from", default=[]): vol.All(cv.ensure_list, [cv.string]),
				vol.Optional("reassign_to", default=[]): vol.All(cv.ensure_list, [cv.string]),
			}),
		),
		"adjust_energy": (
			adjust_energy_service,
			vol.Schema({
				vol.Required("entity_id"): cv.entity_id,
				vol.Optional("adjustment_kwh"): vol.Coerce(float),
				vol.Optional("set_to_value"): vol.All(vol.Coerce(float), vol.Range(min=0)),
				vol.Optional("copy_from_entity"): cv.entity_id,
			}),
		),
		"copy_from_previous_hour": (
			_copy_from_previous_hour,
			vol.Schema({
				vol.Optional("target_datetime"): cv.string,
				vol.Optional("source_datetime"): cv.string,
				vol.Optional("hour_to_fix"): cv.string,
				vol.Optional("hours_back", default=1): vol.All(vol.Coerce(int), vol.Range(min=1)),
			}),
		),
		"reset_statistical_tracking": (reset_statistical_tracking_service, vol.Schema({})),
	}
	for name, (handler, schema) in services.items():
		if hass.services.has_service(DOMAIN, name):
			continue

		async def _handle(call: ServiceCall, handler=handler) -> None:
			await handler(hass, call)

		hass.services.async_register(DOMAIN, name, _handle, schema=schema)


async def generate_sensors_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Rebuild entities from the saved options (same as reloading the integration)."""
	entry, _ = _loaded_entry(hass)
	await hass.config_entries.async_reload(entry.entry_id)


def _parse_base_names(raw) -> set[str]:
	if not raw:
		return set()
	items = raw.split(",") if isinstance(raw, str) else raw
	return {str(item).strip() for item in items if str(item).strip()}


async def reset_energy_sensors_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Scale (or zero) stored totals, e.g. to correct doubled values."""
	reset_factor = call.data["reset_factor"]
	reset_to_zero = call.data["reset_to_zero"]
	selected = _parse_base_names(call.data.get("sensors")) | _parse_base_names(call.data.get("selected_sensors"))

	entities = _adjustable_entities(hass)
	if selected:
		unknown = selected - {entity._base_name for entity in entities}
		if unknown:
			raise ServiceValidationError(f"Unknown sensor base names: {', '.join(sorted(unknown))}")
		entities = [entity for entity in entities if entity._base_name in selected]

	for entity in entities:
		old_value = entity.total
		new_value = 0.0 if reset_to_zero else old_value * reset_factor
		await entity.async_set_total(new_value)
		_LOGGER.info("Reset %s: %.4f kWh -> %.4f kWh", entity.entity_id, old_value, new_value)

	_LOGGER.info("Reset %d energy sensors", len(entities))


async def debug_sensor_detection_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Log detected power sensors and the state of each configured source."""
	entry, entry_data = _loaded_entry(hass)
	detected = detect_power_sensors(hass)
	_LOGGER.info("=== Energy Sensor Generator: sensor detection ===")
	_LOGGER.info("Detected %d power sensors: %s", len(detected), detected)

	for entity_id in entry.options.get("selected_power_sensors", []) or []:
		state = hass.states.get(entity_id)
		if state:
			_LOGGER.info(
				"Selected %s: unit=%r device_class=%r state=%s",
				entity_id,
				state.attributes.get("unit_of_measurement"),
				state.attributes.get("device_class"),
				state.state,
			)
		else:
			_LOGGER.warning("Selected %s: NOT AVAILABLE", entity_id)

	loaded = sorted(entity.entity_id for entity in entry_data.get("entities", {}).values() if entity.hass)
	_LOGGER.info("Loaded generated entities (%d): %s", len(loaded), loaded)
	_LOGGER.info("=== End of detection report ===")


async def migrate_entity_ids_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Rename entity IDs to match their unique IDs (e.g. sensor.<unique_id>)."""
	entry, _ = _loaded_entry(hass)
	entity_registry = er.async_get(hass)
	migrated_count = 0
	skipped_count = 0
	error_count = 0

	for reg_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
		unique_id = reg_entry.unique_id
		if not (unique_id.endswith("_energy") or "_energy_" in unique_id):
			continue  # price adjustments and other non-energy sensors
		expected_entity_id = f"sensor.{unique_id}"
		if reg_entry.entity_id == expected_entity_id:
			continue
		if entity_registry.async_get(expected_entity_id):
			_LOGGER.warning("Cannot migrate %s to %s - target already exists", reg_entry.entity_id, expected_entity_id)
			skipped_count += 1
			continue
		try:
			entity_registry.async_update_entity(reg_entry.entity_id, new_entity_id=expected_entity_id)
			_LOGGER.info("Migrated %s -> %s", reg_entry.entity_id, expected_entity_id)
			migrated_count += 1
		except ValueError as err:
			_LOGGER.error("Failed to migrate %s: %s", reg_entry.entity_id, err)
			error_count += 1

	_notify(
		hass,
		"Entity ID Migration Complete",
		f"Migrated {migrated_count} entities\nSkipped {skipped_count} (conflicts)\nErrors: {error_count}",
		"energy_sensor_migration",
	)


async def diagnose_sensor_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Log diagnostics for one generated sensor (matched by entity_id or name)."""
	sensor_name = call.data["sensor_name"].strip()
	_, entry_data = _loaded_entry(hass)
	entity_registry = er.async_get(hass)

	candidates = [
		reg_entry for reg_entry in entity_registry.entities.values()
		if reg_entry.platform == DOMAIN
	]
	match = next((reg_entry for reg_entry in candidates if reg_entry.entity_id == sensor_name), None)
	if match is None:
		needle = sensor_name.lower()
		match = next(
			(
				reg_entry for reg_entry in candidates
				if needle in reg_entry.entity_id.lower()
				or needle in (reg_entry.name or reg_entry.original_name or "").lower()
			),
			None,
		)
	if match is None:
		raise ServiceValidationError(f"No Energy Sensor Generator sensor matches '{sensor_name}'")

	energy_entity = match.entity_id
	state = hass.states.get(energy_entity)
	_LOGGER.info("DIAGNOSIS for %s:", energy_entity)
	if state:
		for key in (
			"last_power", "last_update", "power_to_kw_factor", "source_unit", "calculation_count",
			"calculation_method", "source_current_value", "source_unit_of_measurement", "sample_interval",
		):
			if key in state.attributes:
				_LOGGER.info("  %s: %s", key, state.attributes[key])
		_LOGGER.info("  Current value: %s %s", state.state, state.attributes.get("unit_of_measurement", ""))
	else:
		_LOGGER.info("  No current state")

	entity = entry_data.get("entities", {}).get(match.unique_id)
	source_sensor = getattr(entity, "_source_sensor", None)
	if source_sensor:
		source_state = hass.states.get(source_sensor)
		if source_state:
			_LOGGER.info(
				"SOURCE %s: value=%s unit=%s device_class=%s state_class=%s",
				source_sensor,
				source_state.state,
				source_state.attributes.get("unit_of_measurement"),
				source_state.attributes.get("device_class"),
				source_state.attributes.get("state_class"),
			)
		else:
			_LOGGER.error("SOURCE %s NOT FOUND", source_sensor)

	storage_key = getattr(entity, "storage_key", None)
	if storage_key:
		storage = await entry_data["storage_manager"].async_load()
		_LOGGER.info("STORAGE %s: %s", storage_key, storage.get(storage_key, "no data"))


async def list_sensors_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Log every generated sensor with its value and calculation method."""
	entry, _ = _loaded_entry(hass)
	reg_entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
	_LOGGER.info("Energy Sensor Generator has %d sensors:", len(reg_entries))
	for reg_entry in sorted(reg_entries, key=lambda item: item.entity_id):
		state = hass.states.get(reg_entry.entity_id)
		if state:
			_LOGGER.info(
				"  %s - %s %s, method: %s",
				reg_entry.entity_id,
				state.state,
				state.attributes.get("unit_of_measurement", ""),
				state.attributes.get("calculation_method", "-"),
			)
		else:
			_LOGGER.info("  %s - NOT AVAILABLE", reg_entry.entity_id)


async def export_energy_data_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Export stored totals to a JSON file under the config directory."""
	_, entry_data = _loaded_entry(hass)
	storage = await entry_data["storage_manager"].async_load()
	relative = call.data.get("target_path") or f"energy_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
	target = Path(hass.config.path(relative))

	def _write() -> None:
		target.parent.mkdir(parents=True, exist_ok=True)
		with target.open("w", encoding="utf-8") as handle:
			json.dump(storage, handle, indent=2)

	try:
		await hass.async_add_executor_job(_write)
	except OSError as err:
		raise HomeAssistantError(f"Failed to write {target}: {err}") from err
	_LOGGER.info("Exported energy data to %s", target)
	_notify(hass, "Energy Data Exported", f"Saved to `{target}`", "energy_export")


async def import_energy_data_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Import stored totals from JSON (merge or replace), optionally renaming base names."""
	_, entry_data = _loaded_entry(hass)
	storage_manager = entry_data["storage_manager"]
	source = Path(hass.config.path(call.data["source_path"]))

	def _read() -> dict:
		with source.open("r", encoding="utf-8") as handle:
			return json.load(handle)

	try:
		incoming = await hass.async_add_executor_job(_read)
	except FileNotFoundError as err:
		raise ServiceValidationError(f"Import file not found: {source}") from err
	except (OSError, ValueError) as err:
		raise HomeAssistantError(f"Failed to read {source}: {err}") from err
	if not isinstance(incoming, dict):
		raise ServiceValidationError(f"{source} does not contain exported energy data")

	reassign_from = call.data["reassign_from"]
	reassign_to = call.data["reassign_to"]
	if len(reassign_from) != len(reassign_to):
		raise ServiceValidationError("reassign_from and reassign_to must have the same length")
	reassignment_map = dict(zip(reassign_from, reassign_to))

	def remap_key(key: str) -> str:
		# Keys are like base_energy, base_daily_energy, etc.
		for old, new in reassignment_map.items():
			if key.startswith(old + "_") or key == old:
				return key.replace(old, new, 1)
		return key

	remapped = {remap_key(key): value for key, value in incoming.items()}
	if call.data["mode"] == "replace":
		merged = remapped
	else:
		merged = {**await storage_manager.async_load(), **remapped}
	await storage_manager.async_save(merged)

	# Live entities hold their totals in memory; reload them so the next
	# periodic save does not overwrite the imported values.
	for entity in _adjustable_entities(hass):
		await entity.async_reload_from_storage()

	_LOGGER.info(
		"Imported energy data from %s (mode=%s, %d reassignments)",
		source,
		call.data["mode"],
		len(reassignment_map),
	)


async def reset_statistical_tracking_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Clear statistical window anchors so the next calculation starts afresh."""
	sensors_reset = 0
	for entity in _main_energy_entities(hass):
		if await entity.async_clear_statistical_anchor():
			sensors_reset += 1
			_LOGGER.info("Reset statistical tracking for %s", entity.entity_id)

	_notify(
		hass,
		"Statistical Tracking Reset",
		f"Reset {sensors_reset} sensors.\n\nThe next calculation starts from each sensor's last update "
		"instead of continuing the previous statistical window.",
		"energy_stat_reset",
	)


async def adjust_energy_service(hass: HomeAssistant, call: ServiceCall) -> None:
	"""Add to, set, or copy an energy sensor's total (e.g. to remove a spike)."""
	entity_id = call.data["entity_id"]
	adjustment_kwh = call.data.get("adjustment_kwh")
	set_to_value = call.data.get("set_to_value")
	copy_from_entity = call.data.get("copy_from_entity")

	if sum(value is not None for value in (adjustment_kwh, set_to_value, copy_from_entity)) != 1:
		raise ServiceValidationError("Specify exactly one of: adjustment_kwh, set_to_value, copy_from_entity")

	entity = _live_entity_for(hass, entity_id)
	if not hasattr(entity, "async_set_total"):
		raise ServiceValidationError(f"{entity_id} is not an energy sensor")
	old_value = entity.total

	if adjustment_kwh is not None:
		new_value = old_value + adjustment_kwh
		action = f"adjusted by {adjustment_kwh:+.4f} kWh"
	elif set_to_value is not None:
		new_value = set_to_value
		action = f"set to {set_to_value:.4f} kWh"
	else:
		copy_state = hass.states.get(copy_from_entity)
		try:
			new_value = float(copy_state.state) if copy_state else None
		except (TypeError, ValueError):
			new_value = None
		if new_value is None:
			raise ServiceValidationError(f"{copy_from_entity} has no numeric value to copy")
		action = f"copied from {copy_from_entity} ({new_value:.4f} kWh)"

	if new_value < 0:
		raise ServiceValidationError(f"Adjustment would make {entity_id} negative ({new_value:.4f} kWh)")

	await entity.async_set_total(new_value)
	_LOGGER.info("Energy adjusted for %s: %.4f kWh -> %.4f kWh (%s)", entity_id, old_value, new_value, action)
	_notify(
		hass,
		"Energy Value Adjusted",
		f"**{entity_id}**\n\nOld: {old_value:.4f} kWh\nNew: {new_value:.4f} kWh\n\n{action}",
		f"energy_adjust_{entity_id.replace('.', '_')}",
	)


async def _copy_from_previous_hour(hass: HomeAssistant, call: ServiceCall) -> None:
	await copy_from_previous_hour_service(hass, call, _main_energy_entities(hass))
