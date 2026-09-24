import sys
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_sensor_generator"


def _load_module(name: str, relative_path: str):
	spec = spec_from_file_location(name, BASE_DIR / relative_path)
	module = module_from_spec(spec)
	assert spec and spec.loader
	sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
	spec.loader.exec_module(module)
	return module


naming = _load_module("energy_sensor_generator_naming", "naming.py")


def _constant_base(device):
	return device["switch_entity_id"].split(".", 1)[1] + "_constant"


@pytest.mark.parametrize(
	("entity_id", "base", "main_uid", "storage_key"),
	[
		("sensor.kettle_power", "kettle", "kettle_energy", "kettle_energy"),
		("sensor.smart_plug_2_power", "smart_plug_2", "smart_plug_2_energy", "smart_plug_2_energy"),
		# <root>_power_<n> must not collide with <root>_<n>_power
		("sensor.smart_plug_power_2", "smart_plug_energy_2", "smart_plug_energy_2", "smart_plug_energy_2_energy"),
		("sensor.heat_pump", "heat_pump", "heat_pump_energy", "heat_pump_energy"),
	],
)
def test_persisted_identifiers_are_stable(entity_id, base, main_uid, storage_key):
	"""These strings are stored in the entity registry and storage; changing them orphans sensors."""
	assert naming.power_sensor_base_name(entity_id) == base
	assert naming.main_unique_id(base) == main_uid
	assert naming.main_storage_key(base) == storage_key
	assert naming.period_unique_id(base, "daily") == f"{base}_daily_energy"


def test_unique_id_classification():
	assert naming.is_main_energy_unique_id("kettle_energy")
	assert naming.is_main_energy_unique_id("smart_plug_energy_2")
	assert not naming.is_main_energy_unique_id("kettle_daily_energy")
	assert not naming.is_main_energy_unique_id("smart_plug_energy_2_annual_energy")
	assert not naming.is_main_energy_unique_id("price_adjust_abc")
	assert not naming.is_main_energy_unique_id(naming.SYNTHETIC_GRID_UNIQUE_ID)


def test_plan_sources_drops_duplicates_and_invalid_constants():
	plans = naming.plan_sources(
		["sensor.kettle_power", "sensor.kettle", "", "sensor.oven_power"],
		[
			{"switch_entity_id": "switch.heater", "power_w": 2000, "name": "Heater"},
			{"switch_entity_id": "switch.broken"},
			{"power_w": 100},
		],
		_constant_base,
	)
	assert [(plan.base_name, plan.source_entity_id) for plan in plans] == [
		("kettle", "sensor.kettle_power"),
		("oven", "sensor.oven_power"),
		("heater_constant", "switch.heater"),
	]
	assert plans[0].name_override is None
	assert plans[2].name_override == "Heater"


def test_desired_unique_ids_covers_every_entity_kind():
	plans = naming.plan_sources(["sensor.kettle_power"], [], _constant_base)
	wanted = naming.desired_unique_ids(
		plans,
		naming.enabled_periods({"create_weekly_sensors": False, "create_monthly_sensors": False}),
		[{"id": "abc"}, {"id": ""}],
		synthetic_grid=True,
	)
	assert wanted == {
		"kettle_energy",
		"kettle_daily_energy",
		"kettle_annual_energy",
		"price_adjust_abc",
		naming.SYNTHETIC_GRID_UNIQUE_ID,
	}


@pytest.mark.parametrize(
	("period", "expected"),
	[
		("daily", datetime(2026, 9, 24, tzinfo=timezone.utc)),
		("weekly", datetime(2026, 9, 21, tzinfo=timezone.utc)),  # Monday
		("monthly", datetime(2026, 9, 1, tzinfo=timezone.utc)),
		("annual", datetime(2026, 1, 1, tzinfo=timezone.utc)),
	],
)
def test_period_start(period, expected):
	now = datetime(2026, 9, 24, 15, 30, 12, tzinfo=timezone.utc)  # a Thursday
	assert naming.period_start(period, now) == expected


def test_period_start_on_boundary_is_itself():
	monday_midnight = datetime(2026, 9, 21, tzinfo=timezone.utc)
	assert naming.period_start("weekly", monday_midnight) == monday_midnight
	assert naming.period_start("monthly", monday_midnight) != monday_midnight
