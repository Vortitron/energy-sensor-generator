"""Pure helpers for the power-sensor picker UI.

Kept free of Home Assistant imports so the label and grouping logic can be
unit tested without spinning up a hass object.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping, Sequence

OTHER_SENSORS_GROUP = "Other sensors"

# UI-only keys that must never be written into the config entry options.
OPTIONS_UI_KEYS = (
	"show_advanced",
	"configure_constant_devices",
	"configure_price_adjustments",
	"custom_power_sensor",
	"period_sensors",
	"constant_device_action",
	"constant_device_switch",
	"constant_device_power",
	"constant_device_name",
	"constant_device_remove",
	"price_adjust_action",
	"price_adjust_source",
	"price_adjust_add_amount",
	"price_adjust_name",
	"price_adjust_remove",
	"_constant_devices_status",
	"_price_adjust_status",
)

SAVED_OPTION_KEYS = (
	"selected_power_sensors",
	"create_daily_sensors",
	"create_monthly_sensors",
	"create_weekly_sensors",
	"create_annual_sensors",
	"sample_interval",
	"debug_logging",
	"use_statistical_calculation",
	"create_synthetic_grid_total",
	"force_statistical_only",
	"stat_initial_lookback_minutes",
	"max_energy_per_hour",
	"constant_power_devices",
	"price_adjust_sensors",
)


def _looks_like_entity_id(value: str) -> bool:
	return "." in value and value.split(".", 1)[0].isidentifier()


def _object_id(entity_id: str) -> str:
	return entity_id.split(".", 1)[-1] if entity_id else ""


def _pretty_object_id(entity_id: str) -> str:
	return _object_id(entity_id).replace("_", " ").strip()


def _strip_device_prefix(name: str, device_name: str) -> str:
	"""Remove a redundant device-name prefix from a sensor label."""
	stripped = name.strip()
	device = device_name.strip()
	if not stripped or not device:
		return stripped
	if stripped.lower() == device.lower():
		return ""
	for separator in (" - ", " – ", " — ", ": ", " | "):
		prefix = device + separator
		if stripped.lower().startswith(prefix.lower()):
			return stripped[len(prefix):].strip()
	if stripped.lower().startswith(device.lower() + " "):
		return stripped[len(device):].strip()
	return stripped


def short_sensor_label(
	entity_id: str,
	friendly_name: str | None = None,
	device_name: str | None = None,
) -> str:
	"""Compact label that does not repeat the device name or entity id.

	The options UI groups sensors by device, so the per-row text only needs
	to distinguish sensors on the same device (e.g. L1 / L2 / Power).
	"""
	pretty_object = _pretty_object_id(entity_id) or entity_id
	raw = (friendly_name or "").strip()
	if not raw or raw == entity_id or _looks_like_entity_id(raw):
		name = pretty_object
	else:
		name = raw

	if device_name:
		without_device = _strip_device_prefix(name, device_name)
		if without_device:
			name = without_device
		elif pretty_object.lower() != device_name.strip().lower():
			name = _strip_device_prefix(pretty_object, device_name) or pretty_object
		else:
			name = "Power"

	# Device-prefixed friendly names sometimes leave a raw entity id behind.
	if _looks_like_entity_id(name) or name == entity_id:
		fallback = _strip_device_prefix(pretty_object, device_name or "") or pretty_object
		if device_name and fallback.lower() == device_name.strip().lower():
			name = "Power"
		else:
			name = fallback or "Power"

	return name or pretty_object or entity_id


def uniquify_labels(entity_to_label: Mapping[str, str]) -> dict[str, str]:
	"""Append a short entity suffix when two sensors would share a label."""
	buckets: dict[str, list[str]] = defaultdict(list)
	for entity_id, label in entity_to_label.items():
		buckets[label.strip().lower()].append(entity_id)

	result = dict(entity_to_label)
	for entity_ids in buckets.values():
		if len(entity_ids) < 2:
			continue
		for entity_id in entity_ids:
			suffix = _object_id(entity_id)
			base = entity_to_label[entity_id].strip()
			result[entity_id] = f"{base} ({suffix})" if base else suffix
	return result


def grouped_selector_options(
	items: Sequence[tuple[str, str, str | None]],
) -> list[dict]:
	"""Build Home Assistant SelectSelector options grouped by device.

	``items`` is ``(entity_id, label, device_name)``. Devices with a single
	power sensor are shown as a flat row named after the device. Devices with
	several power sensors (phases, channels) become a labelled group.
	"""
	by_device: dict[str, list[dict]] = {}
	ungrouped: list[dict] = []
	for entity_id, label, device_name in items:
		option = {"value": entity_id, "label": label}
		name = (device_name or "").strip()
		if name:
			by_device.setdefault(name, []).append(option)
		else:
			ungrouped.append(option)

	options: list[dict] = []
	for device_name in sorted(by_device, key=str.lower):
		entries = sorted(by_device[device_name], key=lambda item: item["label"].lower())
		if len(entries) == 1:
			options.append({"value": entries[0]["value"], "label": device_name})
		else:
			options.append({"label": device_name, "options": entries})

	if ungrouped:
		options.append({
			"label": OTHER_SENSORS_GROUP,
			"options": sorted(ungrouped, key=lambda item: item["label"].lower()),
		})
	return options


def options_overview(
	selected_count: int,
	found_count: int,
	constant_count: int,
	price_count: int,
) -> str:
	"""One-line summary for the options menu description."""
	sensor_bit = f"{selected_count} of {found_count} power sensors selected"
	constant_bit = (
		"1 constant load" if constant_count == 1
		else f"{constant_count} constant loads"
	)
	price_bit = (
		"1 price add-on" if price_count == 1
		else f"{price_count} price add-ons"
	)
	return f"{sensor_bit} · {constant_bit} · {price_bit}"


def period_flags_from_selection(selected: Iterable[str] | None) -> dict[str, bool]:
	"""Map the period multi-select into the stored create_* option flags."""
	chosen = set(selected or [])
	return {
		"create_daily_sensors": "daily" in chosen,
		"create_weekly_sensors": "weekly" in chosen,
		"create_monthly_sensors": "monthly" in chosen,
		"create_annual_sensors": "annual" in chosen,
	}


def period_selection_from_flags(options: Mapping[str, object]) -> list[str]:
	"""Inverse of ``period_flags_from_selection`` for form defaults."""
	selected: list[str] = []
	if options.get("create_daily_sensors", True):
		selected.append("daily")
	if options.get("create_weekly_sensors", True):
		selected.append("weekly")
	if options.get("create_monthly_sensors", True):
		selected.append("monthly")
	if options.get("create_annual_sensors", True):
		selected.append("annual")
	return selected


def merge_saved_options(*parts: Mapping[str, object]) -> dict:
	"""Merge option dicts and drop UI-only keys.

	Later mappings win. Missing saved keys are left out so Home Assistant
	defaults in the sensor platform still apply.
	"""
	merged: dict = {}
	for part in parts:
		if not part:
			continue
		merged.update(part)
	for key in OPTIONS_UI_KEYS:
		merged.pop(key, None)
	return {key: merged[key] for key in SAVED_OPTION_KEYS if key in merged}
