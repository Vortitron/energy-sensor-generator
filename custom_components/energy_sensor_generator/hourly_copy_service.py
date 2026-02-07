from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Tuple

try:
	from homeassistant.config_entries import ConfigEntry
	from homeassistant.core import HomeAssistant, ServiceCall, State
	from homeassistant.helpers import entity_registry as er
	from homeassistant.util import dt as dt_util
	from homeassistant.components import recorder
	from homeassistant.components.recorder import statistics
except ImportError:  # pragma: no cover
	# Allow importing this module in unit tests without Home Assistant installed.
	ConfigEntry = object  # type: ignore
	HomeAssistant = object  # type: ignore
	ServiceCall = object  # type: ignore
	State = object  # type: ignore
	er = None  # type: ignore
	recorder = None  # type: ignore
	statistics = None  # type: ignore
	from datetime import timezone
	
	class _DtUtilFallback:
		@staticmethod
		def parse_datetime(value: str, raise_on_error: bool = False) -> datetime:
			try:
				return datetime.fromisoformat(value.replace("Z", "+00:00"))
			except ValueError:
				if raise_on_error:
					raise
				return None  # type: ignore
		
		@staticmethod
		def as_utc(value: datetime) -> datetime:
			if value.tzinfo is None:
				return value.replace(tzinfo=timezone.utc)
			return value.astimezone(timezone.utc)
		
		@staticmethod
		def as_local(value: datetime) -> datetime:
			# Best-effort fallback: keep UTC.
			return _DtUtilFallback.as_utc(value)
	
	dt_util = _DtUtilFallback()

try:
	from .const import DOMAIN
except ImportError:  # pragma: no cover
	DOMAIN = "energy_sensor_generator"

_LOGGER = logging.getLogger(__name__)

# Search windows are deliberately staged so we avoid hammering the recorder
# when the desired timestamp is close to the requested hour.
_PRIMARY_WINDOW = timedelta(minutes=5)
_EXTENDED_WINDOW = timedelta(minutes=65)
_MAX_LOOKBACK = timedelta(hours=6)
_MAX_ERROR_MESSAGES = 5


@dataclass(frozen=True)
class CopyRequest:
	"""Container describing which timestamps should be copied."""

	source_utc: datetime
	hour_to_fix_utc: datetime | None
	hours_back: int


def _parse_service_datetime(raw_value: str) -> datetime:
	"""Parse service datetime strings into timezone-aware UTC datetimes."""
	raw = str(raw_value or "").strip()
	if not raw:
		raise ValueError("Datetime must not be empty")

	parsed = None
	# Primary: Home Assistant parser (treats naive datetimes as local)
	try:
		parsed = dt_util.parse_datetime(raw, raise_on_error=False)
	except Exception:
		parsed = None

	# Fallback: allow common local-time format with a space separator
	if parsed is None:
		try:
			parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
		except ValueError:
			try:
				# Some parsers only accept "T"
				parsed = datetime.fromisoformat(raw.replace(" ", "T", 1).replace("Z", "+00:00"))
			except ValueError as err:
				raise ValueError(
					f"Invalid datetime '{raw_value}'. Use 'YYYY-MM-DD HH:MM:SS' (local time), "
					"or ISO 8601 (e.g. 'YYYY-MM-DDTHH:MM:SS+00:00')."
				) from err

	return dt_util.as_utc(parsed)


def _resolve_copy_request(data: Mapping[str, object]) -> CopyRequest:
	"""Work out which timestamps to use based on service payload."""
	raw_source = data.get("target_datetime") or data.get("source_datetime")
	raw_hour_to_fix = data.get("hour_to_fix")
	raw_hours_back = data.get("hours_back", 1)

	try:
		hours_back = max(1, int(raw_hours_back))
	except (TypeError, ValueError):
		raise ValueError("hours_back must be an integer greater than 0")

	if raw_source:
		source_dt = _parse_service_datetime(str(raw_source))
		target_dt = _parse_service_datetime(str(raw_hour_to_fix)) if raw_hour_to_fix else None
		return CopyRequest(source_utc=source_dt, hour_to_fix_utc=target_dt, hours_back=hours_back)

	if raw_hour_to_fix:
		target_dt = _parse_service_datetime(str(raw_hour_to_fix))
		source_dt = target_dt - timedelta(hours=hours_back)
		return CopyRequest(source_utc=source_dt, hour_to_fix_utc=target_dt, hours_back=hours_back)

	raise ValueError("Provide either 'target_datetime' (known-good hour) or 'hour_to_fix'.")


def _format_local(dt_value: datetime | None) -> str:
	"""Format datetimes in the user's local timezone for logging."""
	if not dt_value:
		return "-"
	return dt_util.as_local(dt_value).strftime("%Y-%m-%d %H:%M")


def _coerce_float(state_value: str | None) -> float | None:
	"""Convert Home Assistant state strings to floats where possible."""
	if state_value in (None, "unknown", "unavailable"):
		return None
	try:
		return float(state_value)
	except (TypeError, ValueError):
		return None


def _pick_best_state(states: Iterable[State], target_dt: datetime) -> Tuple[float, datetime] | None:
	"""Pick the state closest to the requested timestamp (prefer earlier readings)."""
	best_before: Tuple[float, datetime, float] | None = None
	best_overall: Tuple[float, datetime, float] | None = None

	for item in states:
		value = _coerce_float(item.state)
		if value is None:
			continue

		state_time = item.last_updated
		time_diff = (state_time - target_dt).total_seconds()
		abs_diff = abs(time_diff)

		if best_overall is None or abs_diff < best_overall[2]:
			best_overall = (value, state_time, abs_diff)

		if time_diff <= 0 and (best_before is None or abs_diff < best_before[2]):
			best_before = (value, state_time, abs_diff)

	if best_before:
		return best_before[0], best_before[1]
	if best_overall:
		return best_overall[0], best_overall[1]
	return None


async def _fetch_state_near(
	hass: HomeAssistant,
	entity_id: str,
	target_dt: datetime,
) -> Tuple[float, datetime] | None:
	"""Fetch recorder history close to the requested timestamp."""
	from homeassistant.components.recorder import history

	windows = (_PRIMARY_WINDOW, _EXTENDED_WINDOW, _MAX_LOOKBACK)
	for window in windows:
		start_time = target_dt - window
		end_time = target_dt + _PRIMARY_WINDOW
		states = await hass.async_add_executor_job(
			history.state_changes_during_period,
			hass,
			start_time,
			end_time,
			entity_id,
			False,
			False,
			None,
			True,
		)
		entity_states = states.get(entity_id)
		if not entity_states:
			continue
		best = _pick_best_state(entity_states, target_dt)
		if best:
			return best
	return None


def _iter_energy_sensors(hass: HomeAssistant) -> Iterable[str]:
	"""Yield main energy sensor entity_ids for this integration."""
	entity_registry = er.async_get(hass)
	for entity_id, entry in entity_registry.entities.items():
		if entry.platform != DOMAIN or not entity_id.startswith("sensor."):
			continue
		if any(period in entity_id for period in ["_daily_", "_monthly_", "_weekly_", "_annual_"]):
			continue
		yield entity_id


async def copy_from_previous_hour_service(
	hass: HomeAssistant,
	call: ServiceCall,
	entry: ConfigEntry | None = None,
) -> None:
	"""Copy all generated energy sensors to a previous hour's values."""
	if entry is None:
		entries = hass.config_entries.async_entries(DOMAIN)
		if not entries:
			_LOGGER.error("No config entry found for copy_from_previous_hour.")
			return
		entry = entries[0]

	try:
		request = _resolve_copy_request(call.data)
	except ValueError as err:
		_LOGGER.error(str(err))
		return

	storage_manager = hass.data[DOMAIN][entry.entry_id]["storage_manager"]
	entity_registry = er.async_get(hass)
	energy_sensors = list(_iter_energy_sensors(hass))

	if not energy_sensors:
		_LOGGER.error("No energy sensors found to copy")
		return

	source_label = _format_local(request.source_utc)
	target_label = _format_local(request.hour_to_fix_utc)
	_LOGGER.info(
		"Copying %s energy sensors using data from %s%s",
		len(energy_sensors),
		source_label,
		f" to patch {target_label}" if request.hour_to_fix_utc else " (current values)",
	)

	storage = await storage_manager.async_load()
	sensors_updated: list[tuple[str, float, float, datetime]] = []
	errors: list[str] = []
	stats_adjusted = 0
	stats_errors: list[str] = []

	for entity_id in energy_sensors:
		state_tuple = await _fetch_state_near(hass, entity_id, request.source_utc)
		if not state_tuple:
			errors.append(f"{entity_id}: no recorder data near {source_label}")
			continue

		historical_value, historical_time = state_tuple
		entity_entry = entity_registry.async_get(entity_id)
		if not entity_entry:
			errors.append(f"{entity_id}: missing entity registry entry")
			continue

		storage_key = entity_entry.unique_id
		if storage_key not in storage:
			errors.append(f"{entity_id}: storage key '{storage_key}' not found")
			continue

		existing = storage[storage_key]
		if isinstance(existing, dict):
			old_value = existing.get("value", 0.0)
			existing["value"] = historical_value
		else:
			old_value = existing
			storage[storage_key] = historical_value

		sensors_updated.append((entity_id, old_value, historical_value, historical_time))

	if sensors_updated:
		await storage_manager.async_save(storage)
		for entity_id, *_ in sensors_updated:
			try:
				await hass.helpers.entity_component.async_update_entity(entity_id)
			except Exception:
				pass

	# Fix long-term statistics so the Energy dashboard hourly graph matches the correction
	if sensors_updated and request.hour_to_fix_utc:
		adjust_start = request.hour_to_fix_utc.replace(minute=0, second=0, microsecond=0)
		try:
			if recorder is None or statistics is None:
				raise RuntimeError("Recorder statistics not available")
			recorder_instance = recorder.get_instance(hass)
			if not recorder_instance:
				raise RuntimeError("Recorder instance not available")

			for entity_id, old_value, new_value, _ in sensors_updated:
				delta_kwh = new_value - old_value
				if abs(delta_kwh) < 1e-9:
					continue

				state = hass.states.get(entity_id)
				unit = "kWh"
				if state:
					unit = str(state.attributes.get("unit_of_measurement", unit))

				ok = await recorder_instance.async_add_executor_job(
					statistics.adjust_statistics,
					recorder_instance,
					entity_id,
					adjust_start,
					delta_kwh,
					unit,
				)
				if ok is False:
					stats_errors.append(f"{entity_id}: statistics adjustment failed")
				else:
					stats_adjusted += 1
		except Exception as err:
			stats_errors.append(str(err))

	success_count = len(sensors_updated)
	error_count = len(errors)

	if sensors_updated:
		for entity_id, old_value, new_value, hist_time in sensors_updated:
			_LOGGER.info(
				"✓ %s: %.4f → %.4f kWh (historical reading from %s)",
				entity_id,
				old_value,
				new_value,
				_format_local(hist_time),
			)

	if errors:
		snippet = ", ".join(errors[:_MAX_ERROR_MESSAGES])
		additional = "" if error_count <= _MAX_ERROR_MESSAGES else f" (+{error_count - _MAX_ERROR_MESSAGES} more)"
		_LOGGER.warning("Hourly copy errors: %s%s", snippet, additional)

	parts = [f"Source hour: {source_label}"]
	if request.hour_to_fix_utc:
		parts.append(f"Hour to fix: {target_label}")
	parts.append(f"Updated sensors: {success_count}")
	if request.hour_to_fix_utc:
		parts.append(f"Statistics adjusted: {stats_adjusted}")
	parts.append(f"Errors: {error_count}")
	if stats_errors:
		parts.append("Statistics errors: " + ", ".join(stats_errors[:_MAX_ERROR_MESSAGES]))
	notification_msg = "\n".join(parts)

	await hass.services.async_call(
		"persistent_notification",
		"create",
		{
			"title": "Hourly Data Copy Complete",
			"message": notification_msg,
			"notification_id": "energy_hourly_copy",
		},
	)

