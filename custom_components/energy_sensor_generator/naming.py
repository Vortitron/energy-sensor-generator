"""Base names, unique IDs and storage keys for generated entities.

Every place that needs to know which entities a configuration produces goes
through this module, so the sensor platform, the stale-entity cleanup and the
services always agree. Free of Home Assistant imports so it can be unit
tested directly.

The formats here are persisted (entity registry unique IDs and storage keys),
so they must never change for existing sensors.

Configuration lives in config *subentries* (one per source):

- ``power_sensor``   data: ``source_entity_id``, ``periods``, optional ``name``
- ``constant_load``  data: ``switch_entity_id``, ``power_w``, ``periods``, optional ``name``
- ``price_adjust``   data: ``id``, ``source_entity_id``, ``add_amount``, optional ``name``
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable, Mapping, Protocol, Sequence

PERIODS = ("daily", "weekly", "monthly", "annual")

SUBENTRY_POWER_SENSOR = "power_sensor"
SUBENTRY_CONSTANT_LOAD = "constant_load"
SUBENTRY_PRICE_ADJUST = "price_adjust"

SYNTHETIC_GRID_UNIQUE_ID = "synthetic_grid_total_energy"
PRICE_ADJUST_PREFIX = "price_adjust_"

# Options keys from the pre-subentry (version 1) config entry format.
LEGACY_OPTION_KEYS = (
	"selected_power_sensors",
	"constant_power_devices",
	"price_adjust_sensors",
	"create_daily_sensors",
	"create_weekly_sensors",
	"create_monthly_sensors",
	"create_annual_sensors",
)


class SubentryLike(Protocol):
	"""The parts of a Home Assistant ConfigSubentry this module reads."""

	subentry_id: str
	subentry_type: str
	title: str
	data: Mapping


def power_sensor_base_name(entity_id: str) -> str:
	"""Base name for a power sensor, e.g. ``sensor.plug_power`` -> ``plug``.

	``<root>_power_<n>`` maps to ``<root>_energy_<n>`` so it does not collide
	with ``<root>_<n>_power`` (which maps to ``<root>_<n>``).
	"""
	raw_id = entity_id.replace("sensor.", "")
	base = raw_id.replace("_power", "")
	if "_power_" in raw_id:
		root, _, suffix = raw_id.rpartition("_power_")
		if suffix.isdigit():
			base = f"{root}_energy_{suffix}"
	return base.lower()


def main_unique_id(base_name: str) -> str:
	"""Unique ID of the main kWh sensor for a base name."""
	if base_name.endswith("_energy") or "_energy_" in base_name:
		return base_name
	return f"{base_name}_energy"


def main_storage_key(base_name: str) -> str:
	"""Storage key of the main kWh sensor (differs from the unique ID for ``*_energy_<n>`` bases)."""
	return f"{base_name}_energy"


def period_unique_id(base_name: str, period: str) -> str:
	"""Unique ID (and storage key) of a period sensor."""
	return f"{base_name}_{period}_energy"


def is_period_unique_id(unique_id: str) -> bool:
	return any(unique_id.endswith(f"_{period}_energy") for period in PERIODS)


def is_main_energy_unique_id(unique_id: str) -> bool:
	"""True for main kWh sensors; False for period, price and synthetic sensors."""
	if not unique_id:
		return False
	if unique_id == SYNTHETIC_GRID_UNIQUE_ID or unique_id.startswith(PRICE_ADJUST_PREFIX):
		return False
	return not is_period_unique_id(unique_id)


def normalise_periods(value: Iterable[str] | None) -> tuple[str, ...]:
	"""Known periods in canonical order; ``None`` means every period."""
	if value is None:
		return PERIODS
	chosen = {str(item) for item in value}
	return tuple(period for period in PERIODS if period in chosen)


def enabled_periods(options: Mapping[str, object]) -> list[str]:
	"""Periods switched on in legacy (version 1) options; all default to on."""
	return [period for period in PERIODS if options.get(f"create_{period}_sensors", True)]


def subentry_unique_id(subentry_type: str, entity_id: str) -> str:
	"""Unique ID of a subentry; one subentry per source entity and type."""
	return f"{subentry_type}:{entity_id}"


@dataclass(frozen=True)
class SourcePlan:
	"""One configured source and the base name its entities are built from."""

	base_name: str
	source_entity_id: str
	periods: tuple[str, ...] = PERIODS
	subentry_id: str | None = None
	name: str | None = None
	constant_config: dict | None = field(default=None, hash=False, compare=False)

	@property
	def name_override(self) -> str | None:
		return self.name or None


@dataclass(frozen=True)
class PricePlan:
	"""One configured price add-on sensor."""

	config_id: str
	source_entity_id: str
	add_amount: float
	subentry_id: str | None = None
	name: str | None = None

	@property
	def unique_id(self) -> str:
		return f"{PRICE_ADJUST_PREFIX}{self.config_id}"


def _clean_name(value: object) -> str | None:
	name = str(value or "").strip()
	return name or None


def plan_sources(
	subentries: Iterable[SubentryLike],
	constant_base_name: Callable[[Mapping], str],
) -> list[SourcePlan]:
	"""Resolve power-sensor and constant-load subentries to base names.

	Invalid subentries are skipped. The first subentry to claim a base name
	wins; a later one with the same base name would otherwise overwrite the
	first one's unique IDs.
	"""
	plans: list[SourcePlan] = []
	seen: set[str] = set()

	for subentry in subentries:
		data = subentry.data or {}
		constant_config = None
		if subentry.subentry_type == SUBENTRY_POWER_SENSOR:
			source_entity_id = str(data.get("source_entity_id") or "").strip()
			if not source_entity_id:
				continue
			base_name = power_sensor_base_name(source_entity_id)
		elif subentry.subentry_type == SUBENTRY_CONSTANT_LOAD:
			source_entity_id = str(data.get("switch_entity_id") or "").strip()
			power_w = data.get("power_w")
			if not source_entity_id or power_w is None:
				continue
			constant_config = {
				"switch_entity_id": source_entity_id,
				"power_w": power_w,
				"name": _clean_name(data.get("name")),
			}
			base_name = constant_base_name(constant_config)
		else:
			continue

		if base_name in seen:
			continue
		seen.add(base_name)
		plans.append(
			SourcePlan(
				base_name,
				source_entity_id,
				normalise_periods(data.get("periods")),
				subentry.subentry_id,
				_clean_name(data.get("name")),
				constant_config,
			)
		)

	return plans


def plan_price_adjustments(subentries: Iterable[SubentryLike]) -> list[PricePlan]:
	"""Resolve price add-on subentries, skipping invalid ones."""
	plans: list[PricePlan] = []
	seen: set[str] = set()
	for subentry in subentries:
		if subentry.subentry_type != SUBENTRY_PRICE_ADJUST:
			continue
		data = subentry.data or {}
		source_entity_id = str(data.get("source_entity_id") or "").strip()
		config_id = str(data.get("id") or "").strip() or subentry.subentry_id
		if not source_entity_id or not config_id or config_id in seen:
			continue
		try:
			add_amount = float(data.get("add_amount", 0) or 0)
		except (TypeError, ValueError):
			add_amount = 0.0
		seen.add(config_id)
		plans.append(PricePlan(config_id, source_entity_id, add_amount, subentry.subentry_id, _clean_name(data.get("name"))))
	return plans


def desired_unique_ids(
	plans: Sequence[SourcePlan],
	price_plans: Iterable[PricePlan] = (),
	synthetic_grid: bool = False,
) -> set[str]:
	"""Every unique ID the current configuration should have in the registry."""
	wanted: set[str] = set()
	for plan in plans:
		wanted.add(main_unique_id(plan.base_name))
		for period in plan.periods:
			wanted.add(period_unique_id(plan.base_name, period))
	for price in price_plans:
		wanted.add(price.unique_id)
	if synthetic_grid:
		wanted.add(SYNTHETIC_GRID_UNIQUE_ID)
	return wanted


def legacy_subentries(
	options: Mapping[str, object],
	title_for: Callable[[str], str],
) -> list[dict]:
	"""Convert version 1 options into subentry definitions.

	Returns ``{"subentry_type", "title", "unique_id", "data"}`` dicts (the
	shape of ``ConfigSubentryData``). Unique IDs and storage keys of the
	generated entities are unchanged by the migration, so existing totals and
	Energy dashboard history are kept.
	"""
	periods = list(enabled_periods(options))
	result: list[dict] = []
	unique_ids: set[str] = set()

	def _add(subentry_type: str, entity_id: str, title: str, data: dict) -> None:
		unique_id = subentry_unique_id(subentry_type, entity_id)
		if unique_id in unique_ids:
			return
		unique_ids.add(unique_id)
		result.append({"subentry_type": subentry_type, "title": title, "unique_id": unique_id, "data": data})

	for entity_id in options.get("selected_power_sensors") or []:
		entity_id = str(entity_id or "").strip()
		if entity_id:
			_add(SUBENTRY_POWER_SENSOR, entity_id, title_for(entity_id), {
				"source_entity_id": entity_id,
				"periods": periods,
			})

	for device in options.get("constant_power_devices") or []:
		device = device or {}
		switch_entity_id = str(device.get("switch_entity_id") or "").strip()
		power_w = device.get("power_w")
		if not switch_entity_id or power_w is None:
			continue
		try:
			power_w = float(power_w)
		except (TypeError, ValueError):
			continue
		data: dict = {"switch_entity_id": switch_entity_id, "power_w": power_w, "periods": periods}
		name = _clean_name(device.get("name"))
		if name:
			data["name"] = name
		_add(SUBENTRY_CONSTANT_LOAD, switch_entity_id, name or title_for(switch_entity_id), data)

	for item in options.get("price_adjust_sensors") or []:
		item = item or {}
		source_entity_id = str(item.get("source_entity_id") or "").strip()
		if not source_entity_id:
			continue
		try:
			add_amount = float(item.get("add_amount", 0) or 0)
		except (TypeError, ValueError):
			add_amount = 0.0
		data = {
			"id": str(item.get("id") or "").strip() or uuid.uuid4().hex,
			"source_entity_id": source_entity_id,
			"add_amount": add_amount,
		}
		name = _clean_name(item.get("name"))
		if name:
			data["name"] = name
		_add(SUBENTRY_PRICE_ADJUST, source_entity_id, name or price_title(title_for(source_entity_id), add_amount), data)

	return result


def strip_legacy_options(options: Mapping[str, object]) -> dict:
	"""Options with the keys that moved into subentries removed."""
	return {key: value for key, value in options.items() if key not in LEGACY_OPTION_KEYS}


def price_title(source_title: str, add_amount: float) -> str:
	"""Default title of a price add-on subentry, e.g. ``Nordpool + 0.25``."""
	sign = "-" if add_amount < 0 else "+"
	return f"{source_title} {sign} {abs(add_amount):g}"


def period_start(period: str, now: datetime) -> datetime:
	"""Start of the period containing ``now`` (local midnight, same tzinfo as ``now``)."""
	midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
	if period == "daily":
		return midnight
	if period == "weekly":
		return midnight - timedelta(days=midnight.weekday())
	if period == "monthly":
		return midnight.replace(day=1)
	if period == "annual":
		return midnight.replace(month=1, day=1)
	raise ValueError(f"Unknown period: {period}")
