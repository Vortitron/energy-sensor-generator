"""Base names, unique IDs and storage keys for generated entities.

Every place that needs to know which entities a configuration produces goes
through this module, so the sensor platform, the stale-entity cleanup and the
services always agree. Free of Home Assistant imports so it can be unit
tested directly.

The formats here are persisted (entity registry unique IDs and storage keys),
so they must never change for existing sensors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable, Mapping, Sequence

PERIODS = ("daily", "weekly", "monthly", "annual")

SYNTHETIC_GRID_UNIQUE_ID = "synthetic_grid_total_energy"
PRICE_ADJUST_PREFIX = "price_adjust_"


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


def enabled_periods(options: Mapping[str, object]) -> list[str]:
	"""Periods switched on in the options (all default to on)."""
	return [period for period in PERIODS if options.get(f"create_{period}_sensors", True)]


@dataclass(frozen=True)
class SourcePlan:
	"""One configured source and the base name its entities are built from."""

	base_name: str
	source_entity_id: str
	constant_config: dict | None = field(default=None, hash=False, compare=False)

	@property
	def name_override(self) -> str | None:
		if self.constant_config:
			return self.constant_config.get("name") or None
		return None


def plan_sources(
	selected_power_sensors: Iterable[str] | None,
	constant_devices: Iterable[Mapping] | None,
	constant_base_name: Callable[[Mapping], str],
) -> list[SourcePlan]:
	"""Resolve configured sources to base names, dropping duplicates and invalid entries.

	The first source to claim a base name wins; a later source with the same
	base name would otherwise overwrite the first one's unique IDs.
	"""
	plans: list[SourcePlan] = []
	seen: set[str] = set()

	for entity_id in selected_power_sensors or []:
		if not entity_id:
			continue
		base_name = power_sensor_base_name(entity_id)
		if base_name in seen:
			continue
		seen.add(base_name)
		plans.append(SourcePlan(base_name, entity_id))

	for device in constant_devices or []:
		switch_entity = (device or {}).get("switch_entity_id")
		if not switch_entity or device.get("power_w") is None:
			continue
		base_name = constant_base_name(device)
		if base_name in seen:
			continue
		seen.add(base_name)
		plans.append(SourcePlan(base_name, switch_entity, dict(device)))

	return plans


def desired_unique_ids(
	plans: Sequence[SourcePlan],
	periods: Iterable[str],
	price_adjustments: Iterable[Mapping] | None = None,
	synthetic_grid: bool = False,
) -> set[str]:
	"""Every unique ID the current configuration should have in the registry."""
	periods = list(periods)
	wanted: set[str] = set()
	for plan in plans:
		wanted.add(main_unique_id(plan.base_name))
		for period in periods:
			wanted.add(period_unique_id(plan.base_name, period))
	for item in price_adjustments or []:
		config_id = str((item or {}).get("id") or "").strip()
		if config_id:
			wanted.add(f"{PRICE_ADJUST_PREFIX}{config_id}")
	if synthetic_grid:
		wanted.add(SYNTHETIC_GRID_UNIQUE_ID)
	return wanted


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
