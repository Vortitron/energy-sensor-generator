import sys
from datetime import datetime, timezone
from types import SimpleNamespace
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


def _sub(subentry_type, data, subentry_id="id", title="t"):
	return SimpleNamespace(subentry_id=subentry_id, subentry_type=subentry_type, title=title, data=data)


def test_plan_sources_drops_duplicates_and_invalid_subentries():
	plans = naming.plan_sources(
		[
			_sub(naming.SUBENTRY_POWER_SENSOR, {"source_entity_id": "sensor.kettle_power", "periods": ["annual", "daily", "bogus"]}, "s1"),
			_sub(naming.SUBENTRY_POWER_SENSOR, {"source_entity_id": "sensor.kettle"}, "s2"),  # same base name
			_sub(naming.SUBENTRY_POWER_SENSOR, {"source_entity_id": ""}, "s3"),
			_sub(naming.SUBENTRY_POWER_SENSOR, {"source_entity_id": "sensor.oven_power", "name": " Oven "}, "s4"),
			_sub(naming.SUBENTRY_CONSTANT_LOAD, {"switch_entity_id": "switch.heater", "power_w": 2000, "name": "Heater"}, "s5"),
			_sub(naming.SUBENTRY_CONSTANT_LOAD, {"switch_entity_id": "switch.broken"}, "s6"),
			_sub(naming.SUBENTRY_PRICE_ADJUST, {"source_entity_id": "sensor.price", "add_amount": 1}, "s7"),
			_sub("unknown", {"source_entity_id": "sensor.x_power"}, "s8"),
		],
		_constant_base,
	)
	assert [(plan.base_name, plan.source_entity_id, plan.subentry_id) for plan in plans] == [
		("kettle", "sensor.kettle_power", "s1"),
		("oven", "sensor.oven_power", "s4"),
		("heater_constant", "switch.heater", "s5"),
	]
	# Unknown periods are dropped and the rest keep canonical order; missing means all
	assert plans[0].periods == ("daily", "annual")
	assert plans[1].periods == naming.PERIODS
	assert plans[0].name_override is None
	assert plans[1].name_override == "Oven"
	assert plans[2].name_override == "Heater"
	assert plans[2].constant_config == {"switch_entity_id": "switch.heater", "power_w": 2000, "name": "Heater"}


def test_plan_price_adjustments_uses_stored_id_or_subentry_id():
	plans = naming.plan_price_adjustments([
		_sub(naming.SUBENTRY_PRICE_ADJUST, {"id": "abc", "source_entity_id": "sensor.a", "add_amount": "0.5"}, "s1"),
		_sub(naming.SUBENTRY_PRICE_ADJUST, {"source_entity_id": "sensor.b", "add_amount": None, "name": "B"}, "s2"),
		_sub(naming.SUBENTRY_PRICE_ADJUST, {"source_entity_id": ""}, "s3"),
		_sub(naming.SUBENTRY_POWER_SENSOR, {"source_entity_id": "sensor.c_power"}, "s4"),
	])
	assert [(plan.unique_id, plan.source_entity_id, plan.add_amount, plan.name) for plan in plans] == [
		("price_adjust_abc", "sensor.a", 0.5, None),
		("price_adjust_s2", "sensor.b", 0.0, "B"),
	]


def test_desired_unique_ids_covers_every_entity_kind():
	plans = naming.plan_sources(
		[_sub(naming.SUBENTRY_POWER_SENSOR, {"source_entity_id": "sensor.kettle_power", "periods": ["daily", "annual"]})],
		_constant_base,
	)
	prices = naming.plan_price_adjustments([
		_sub(naming.SUBENTRY_PRICE_ADJUST, {"id": "abc", "source_entity_id": "sensor.p", "add_amount": 0}),
	])
	wanted = naming.desired_unique_ids(plans, prices, synthetic_grid=True)
	assert wanted == {
		"kettle_energy",
		"kettle_daily_energy",
		"kettle_annual_energy",
		"price_adjust_abc",
		naming.SYNTHETIC_GRID_UNIQUE_ID,
	}


def test_legacy_subentries_keep_ids_and_apply_global_periods():
	options = {
		"selected_power_sensors": ["sensor.kettle_power", "", "sensor.kettle_power", "sensor.oven_power"],
		"constant_power_devices": [
			{"switch_entity_id": "switch.heater", "power_w": "2000", "name": "Heater"},
			{"switch_entity_id": "switch.broken"},
		],
		"price_adjust_sensors": [
			{"id": "abc", "source_entity_id": "sensor.price", "add_amount": 0.25},
			{"source_entity_id": "sensor.price2", "add_amount": -0.1, "name": "Minus"},
			{"source_entity_id": "sensor.price"},  # duplicate source
		],
		"create_weekly_sensors": False,
		"sample_interval": 30,
	}
	result = naming.legacy_subentries(options, lambda entity_id: entity_id.split(".")[1].title())

	assert [(item["subentry_type"], item["unique_id"], item["title"]) for item in result] == [
		("power_sensor", "power_sensor:sensor.kettle_power", "Kettle_Power"),
		("power_sensor", "power_sensor:sensor.oven_power", "Oven_Power"),
		("constant_load", "constant_load:switch.heater", "Heater"),
		("price_adjust", "price_adjust:sensor.price", "Price + 0.25"),
		("price_adjust", "price_adjust:sensor.price2", "Minus"),
	]
	assert result[0]["data"] == {"source_entity_id": "sensor.kettle_power", "periods": ["daily", "monthly", "annual"]}
	assert result[2]["data"] == {
		"switch_entity_id": "switch.heater", "power_w": 2000.0, "periods": ["daily", "monthly", "annual"], "name": "Heater",
	}
	assert result[3]["data"]["id"] == "abc"
	assert len(result[4]["data"]["id"]) == 32  # generated
	assert naming.strip_legacy_options(options) == {"sample_interval": 30}


def test_price_title_formats_sign():
	assert naming.price_title("Nordpool", 0.25) == "Nordpool + 0.25"
	assert naming.price_title("Nordpool", -0.5) == "Nordpool - 0.5"
	assert naming.price_title("Nordpool", 0) == "Nordpool + 0"


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
