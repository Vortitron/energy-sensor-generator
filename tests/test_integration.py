"""End-to-end tests against a real Home Assistant core.

Requires pytest-homeassistant-custom-component; skipped when it is not installed.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_sensor_generator.const import DOMAIN
from custom_components.energy_sensor_generator.naming import (
	SUBENTRY_CONSTANT_LOAD,
	SUBENTRY_POWER_SENSOR,
	SUBENTRY_PRICE_ADJUST,
	subentry_unique_id,
)

STORAGE_KEY = "energy_sensor_generator"
ALL_PERIODS = ["daily", "weekly", "monthly", "annual"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
	yield


@pytest.fixture
def expected_lingering_timers() -> bool:
	# Energy sensors align their interval timers to the wall clock
	return True


def _power(hass: HomeAssistant, entity_id: str, value: float, name: str) -> None:
	hass.states.async_set(
		entity_id,
		str(value),
		{"unit_of_measurement": "W", "device_class": "power", "friendly_name": name},
	)


def _power_subentry(entity_id: str, periods: list[str] | None = None, title: str | None = None) -> ConfigSubentryData:
	return ConfigSubentryData(
		subentry_type=SUBENTRY_POWER_SENSOR,
		title=title or entity_id,
		unique_id=subentry_unique_id(SUBENTRY_POWER_SENSOR, entity_id),
		data={"source_entity_id": entity_id, "periods": ALL_PERIODS if periods is None else periods},
	)


async def _setup(
	hass: HomeAssistant,
	sources: list[str] | None = None,
	options: dict | None = None,
	subentries: list[ConfigSubentryData] | None = None,
	periods: list[str] | None = None,
) -> MockConfigEntry:
	entry = MockConfigEntry(
		domain=DOMAIN,
		unique_id=DOMAIN,
		version=2,
		data={},
		options=options or {},
		subentries_data=(subentries or []) + [_power_subentry(entity_id, periods) for entity_id in sources or []],
	)
	entry.add_to_hass(hass)
	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()
	return entry


def _entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
	return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)


def _subentry(entry: MockConfigEntry, subentry_type: str, entity_id: str):
	wanted = subentry_unique_id(subentry_type, entity_id)
	return next(sub for sub in entry.subentries.values() if sub.unique_id == wanted)


def _schema_field(result, name: str):
	return next(value for key, value in result["data_schema"].schema.items() if str(key) == name)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

async def test_creates_main_and_period_sensors(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kitchen Kettle Power")
	_power(hass, "sensor.plug_power_2", 0, "Plug Two Power")

	entry = await _setup(hass, ["sensor.kettle_power", "sensor.plug_power_2"], periods=["daily"])

	# Entity IDs come from the friendly name, unique IDs from the source entity
	assert _entity_id(hass, "kettle_energy") == "sensor.kitchen_kettle_energy"
	assert _entity_id(hass, "kettle_daily_energy") == "sensor.kitchen_kettle_daily_energy"
	# <root>_power_<n> is disambiguated to <root>_energy_<n>
	assert _entity_id(hass, "plug_energy_2") is not None
	assert _entity_id(hass, "plug_energy_2_daily_energy") is not None
	assert _entity_id(hass, "kettle_weekly_energy") is None

	# Entities belong to the subentry of their source
	registry = er.async_get(hass)
	kettle = _subentry(entry, SUBENTRY_POWER_SENSOR, "sensor.kettle_power")
	assert registry.async_get("sensor.kitchen_kettle_energy").config_subentry_id == kettle.subentry_id
	assert registry.async_get("sensor.kitchen_kettle_daily_energy").config_subentry_id == kettle.subentry_id


async def test_period_sensor_follows_name_based_main_entity(hass: HomeAssistant) -> None:
	"""Periods used to track sensor.<base>_energy, which does not exist when HA names the entity after its friendly name."""
	_power(hass, "sensor.kettle_power", 0, "Kitchen Kettle Power")
	await _setup(hass, ["sensor.kettle_power"])

	await hass.services.async_call(
		DOMAIN, "adjust_energy",
		{"entity_id": "sensor.kitchen_kettle_energy", "adjustment_kwh": 1.5},
		blocking=True,
	)
	await hass.async_block_till_done()
	await hass.services.async_call(
		DOMAIN, "adjust_energy",
		{"entity_id": "sensor.kitchen_kettle_energy", "adjustment_kwh": 2.0},
		blocking=True,
	)
	await hass.async_block_till_done()

	assert float(hass.states.get("sensor.kitchen_kettle_energy").state) == pytest.approx(3.5)
	daily_before_rename = float(hass.states.get("sensor.kitchen_kettle_daily_energy").state)
	assert daily_before_rename in (pytest.approx(2.0), pytest.approx(3.5))

	# Renaming the main entity must not break tracking
	er.async_get(hass).async_update_entity("sensor.kitchen_kettle_energy", new_entity_id="sensor.kettle_total")
	await hass.async_block_till_done()
	await hass.services.async_call(
		DOMAIN, "adjust_energy",
		{"entity_id": "sensor.kettle_total", "adjustment_kwh": 1.0},
		blocking=True,
	)
	await hass.async_block_till_done()
	assert float(hass.states.get("sensor.kitchen_kettle_daily_energy").state) == pytest.approx(daily_before_rename + 1.0)


async def test_period_sensor_counts_from_a_zero_anchor(hass: HomeAssistant) -> None:
	"""A main sensor that starts at exactly 0 kWh must not lose its first increase."""
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	await _setup(hass, ["sensor.kettle_power"], periods=["daily"])
	assert hass.states.get("sensor.kettle_energy").state == "0.0"

	await hass.services.async_call(
		DOMAIN, "adjust_energy", {"entity_id": "sensor.kettle_energy", "adjustment_kwh": 0.25}, blocking=True
	)
	await hass.async_block_till_done()
	assert float(hass.states.get("sensor.kettle_daily_energy").state) == pytest.approx(0.25)


async def test_adjust_energy_survives_the_next_save(hass: HomeAssistant, hass_storage) -> None:
	"""Services used to edit storage directly and the live entity overwrote it on its next save."""
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, ["sensor.kettle_power"])

	await hass.services.async_call(
		DOMAIN, "adjust_energy",
		{"entity_id": "sensor.kettle_energy", "set_to_value": 12.25},
		blocking=True,
	)
	assert hass.states.get("sensor.kettle_energy").state == "12.25"

	entity = hass.data[DOMAIN][entry.entry_id]["entities"]["kettle_energy"]
	await entity._save_state()
	assert await hass.config_entries.async_unload(entry.entry_id)
	await hass.async_block_till_done()
	assert hass_storage[STORAGE_KEY]["data"]["kettle_energy"]["value"] == pytest.approx(12.25)


async def test_reset_energy_sensors_scales_only_selected(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	_power(hass, "sensor.b_power", 0, "B Power")
	await _setup(hass, ["sensor.a_power", "sensor.b_power"], periods=[])
	for entity_id in ("sensor.a_energy", "sensor.b_energy"):
		await hass.services.async_call(
			DOMAIN, "adjust_energy", {"entity_id": entity_id, "set_to_value": 10}, blocking=True
		)

	await hass.services.async_call(
		DOMAIN, "reset_energy_sensors", {"sensors": "a", "reset_factor": 0.5}, blocking=True
	)
	assert float(hass.states.get("sensor.a_energy").state) == pytest.approx(5)
	assert float(hass.states.get("sensor.b_energy").state) == pytest.approx(10)

	with pytest.raises(ServiceValidationError):
		await hass.services.async_call(
			DOMAIN, "reset_energy_sensors", {"sensors": "nope"}, blocking=True
		)


async def test_adjust_energy_rejects_bad_input(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	await _setup(hass, ["sensor.kettle_power"])

	with pytest.raises(ServiceValidationError):
		await hass.services.async_call(
			DOMAIN, "adjust_energy",
			{"entity_id": "sensor.kettle_energy", "adjustment_kwh": 1, "set_to_value": 2},
			blocking=True,
		)
	with pytest.raises(ServiceValidationError):
		await hass.services.async_call(
			DOMAIN, "adjust_energy",
			{"entity_id": "sensor.kettle_power", "adjustment_kwh": 1},
			blocking=True,
		)
	with pytest.raises(ServiceValidationError):
		await hass.services.async_call(
			DOMAIN, "adjust_energy",
			{"entity_id": "sensor.kettle_energy", "adjustment_kwh": -5},
			blocking=True,
		)


async def test_removing_a_subentry_removes_its_entities(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	_power(hass, "sensor.b_power", 0, "B Power")
	entry = await _setup(hass, ["sensor.a_power", "sensor.b_power"])
	assert _entity_id(hass, "b_energy") is not None
	assert _entity_id(hass, "b_annual_energy") is not None

	hass.config_entries.async_remove_subentry(entry, _subentry(entry, SUBENTRY_POWER_SENSOR, "sensor.b_power").subentry_id)
	await hass.async_block_till_done()

	assert entry.state is ConfigEntryState.LOADED
	assert _entity_id(hass, "a_energy") is not None
	assert _entity_id(hass, "b_energy") is None
	assert _entity_id(hass, "b_annual_energy") is None
	assert hass.states.get("sensor.a_energy") is not None


async def test_changing_periods_reloads_and_removes_period_sensor(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	entry = await _setup(hass, ["sensor.a_power"])
	assert _entity_id(hass, "a_annual_energy") is not None

	subentry = _subentry(entry, SUBENTRY_POWER_SENSOR, "sensor.a_power")
	hass.config_entries.async_update_subentry(
		entry, subentry, data={**subentry.data, "periods": ["daily"]}
	)
	await hass.async_block_till_done()

	assert entry.state is ConfigEntryState.LOADED
	assert _entity_id(hass, "a_daily_energy") is not None
	assert _entity_id(hass, "a_annual_energy") is None


async def test_missed_period_reset_is_caught_up_on_start(hass: HomeAssistant, hass_storage) -> None:
	two_days_ago = (dt_util.now() - timedelta(days=2)).isoformat()
	hass_storage[STORAGE_KEY] = {
		"version": 1,
		"minor_version": 1,
		"key": STORAGE_KEY,
		"data": {
			"kettle_energy": {"value": 40.0},
			"kettle_daily_energy": {"value": 7.0, "last_reset": two_days_ago, "last_energy": 40.0},
			"kettle_annual_energy": {"value": 30.0, "last_reset": dt_util.now().isoformat(), "last_energy": 40.0},
		},
	}
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	await _setup(hass, ["sensor.kettle_power"])

	assert float(hass.states.get("sensor.kettle_energy").state) == pytest.approx(40)
	assert float(hass.states.get("sensor.kettle_daily_energy").state) == 0
	assert float(hass.states.get("sensor.kettle_annual_energy").state) == pytest.approx(30)


async def test_synthetic_grid_total_sums_main_sensors(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	_power(hass, "sensor.b_power_2", 0, "B Power")
	await _setup(hass, ["sensor.a_power", "sensor.b_power_2"], options={"create_synthetic_grid_total": True})
	grid = _entity_id(hass, "synthetic_grid_total_energy")
	for unique_id, value in (("a_energy", 2.0), ("b_energy_2", 3.0)):
		await hass.services.async_call(
			DOMAIN, "adjust_energy",
			{"entity_id": _entity_id(hass, unique_id), "set_to_value": value},
			blocking=True,
		)
	await hass.async_block_till_done()
	# b_energy_2 (a disambiguated main sensor) used to be left out of the total
	assert float(hass.states.get(grid).state) == pytest.approx(5.0)


async def test_services_are_real_coroutines(hass: HomeAssistant) -> None:
	"""Services registered as lambdas ran in an executor and were never awaited."""
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, ["sensor.kettle_power"])

	await hass.services.async_call(DOMAIN, "generate_sensors", {}, blocking=True)
	await hass.async_block_till_done()
	assert entry.state is ConfigEntryState.LOADED
	assert hass.states.get("sensor.kettle_energy") is not None


# ---------------------------------------------------------------------------
# Migration from the version 1 options format
# ---------------------------------------------------------------------------

async def test_migrates_v1_options_to_subentries(hass: HomeAssistant, hass_storage) -> None:
	hass_storage[STORAGE_KEY] = {
		"version": 1,
		"minor_version": 1,
		"key": STORAGE_KEY,
		"data": {"kettle_energy": {"value": 123.5}},
	}
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	_power(hass, "sensor.gone_power", 0, "Gone Power")
	hass.states.async_set("switch.heater", "on", {"friendly_name": "Heater"})
	hass.states.async_set("sensor.nordpool", "1.5", {"unit_of_measurement": "SEK/kWh", "friendly_name": "Nordpool"})

	entry = MockConfigEntry(
		domain=DOMAIN,
		unique_id=DOMAIN,
		version=1,
		data={"sample_interval": 60},
		options={
			"selected_power_sensors": ["sensor.kettle_power", "sensor.gone_power", "sensor.kettle_power"],
			"constant_power_devices": [{"switch_entity_id": "switch.heater", "power_w": 2000, "name": "Big Heater"}],
			"price_adjust_sensors": [{"id": "abc123", "source_entity_id": "sensor.nordpool", "add_amount": 0.25}],
			"create_daily_sensors": False,
			"create_weekly_sensors": True,
			"create_monthly_sensors": False,
			"create_annual_sensors": True,
			"sample_interval": 30,
			"debug_logging": True,
			"max_energy_per_hour": 12,
		},
	)
	entry.add_to_hass(hass)
	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()

	assert entry.version == 2
	assert entry.state is ConfigEntryState.LOADED
	# Lists moved into subentries; global settings stayed in options
	assert "selected_power_sensors" not in entry.options
	assert "create_daily_sensors" not in entry.options
	assert entry.options["sample_interval"] == 30
	assert entry.options["max_energy_per_hour"] == 12

	by_type = {}
	for sub in entry.subentries.values():
		by_type.setdefault(sub.subentry_type, []).append(sub)
	assert sorted(sub.title for sub in by_type[SUBENTRY_POWER_SENSOR]) == ["Gone", "Kettle"]
	assert by_type[SUBENTRY_POWER_SENSOR][0].data["periods"] == ["weekly", "annual"]
	assert by_type[SUBENTRY_CONSTANT_LOAD][0].title == "Big Heater"
	assert by_type[SUBENTRY_CONSTANT_LOAD][0].data["power_w"] == 2000.0
	assert by_type[SUBENTRY_PRICE_ADJUST][0].data["id"] == "abc123"

	# Same unique IDs as before, so the stored total is still there
	assert float(hass.states.get("sensor.kettle_energy").state) == pytest.approx(123.5)
	assert _entity_id(hass, "kettle_weekly_energy") is not None
	assert _entity_id(hass, "kettle_daily_energy") is None
	assert _entity_id(hass, "heater_constant_energy") is not None
	assert _entity_id(hass, "price_adjust_abc123") is not None
	assert float(hass.states.get(_entity_id(hass, "price_adjust_abc123")).state) == pytest.approx(1.75)


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------

async def test_config_flow_creates_entry_with_chosen_sensors(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	_power(hass, "sensor.b_power", 0, "B Power")
	hass.states.async_set("sensor.not_power", "5", {"unit_of_measurement": "%"})

	result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
	assert result["type"] is FlowResultType.FORM
	picker = _schema_field(result, "power_sensors")
	assert sorted(picker.config["include_entities"]) == ["sensor.a_power", "sensor.b_power"]
	assert picker.config["multiple"] is True

	result = await hass.config_entries.flow.async_configure(
		result["flow_id"], {"power_sensors": ["sensor.b_power", "sensor.b_power"], "periods": ["daily"]}
	)
	assert result["type"] is FlowResultType.CREATE_ENTRY
	await hass.async_block_till_done()

	entry = result["result"]
	assert entry.version == 2
	assert [sub.title for sub in entry.subentries.values()] == ["B"]
	assert _entity_id(hass, "b_energy") is not None
	assert _entity_id(hass, "b_daily_energy") is not None
	assert _entity_id(hass, "b_weekly_energy") is None
	assert _entity_id(hass, "a_energy") is None


async def test_add_power_sensor_subentry_flow(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	_power(hass, "sensor.b_power", 0, "B Power")
	entry = await _setup(hass, ["sensor.a_power"])

	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_POWER_SENSOR), context={"source": config_entries.SOURCE_USER}
	)
	assert result["type"] is FlowResultType.FORM
	# Already-tracked sensors are not offered again
	assert _schema_field(result, "source_entity_id").config["include_entities"] == ["sensor.b_power"]
	assert result["description_placeholders"] == {"count": "1"}

	# The picker only accepts listed sensors, so a tracked one cannot be submitted
	with pytest.raises(InvalidData):
		await hass.config_entries.subentries.async_configure(
			result["flow_id"], {"source_entity_id": "sensor.a_power", "periods": ["daily"]}
		)

	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"source_entity_id": "sensor.b_power", "periods": ["daily"]}
	)
	assert result["type"] is FlowResultType.CREATE_ENTRY
	assert result["title"] == "B"
	await hass.async_block_till_done()

	assert entry.state is ConfigEntryState.LOADED
	assert _entity_id(hass, "b_energy") is not None
	assert _entity_id(hass, "b_daily_energy") is not None
	assert _entity_id(hass, "b_monthly_energy") is None
	subentry = _subentry(entry, SUBENTRY_POWER_SENSOR, "sensor.b_power")
	assert subentry.data == {"source_entity_id": "sensor.b_power", "periods": ["daily"]}
	registry = er.async_get(hass)
	assert registry.async_get(_entity_id(hass, "b_energy")).config_subentry_id == subentry.subentry_id

	# Nothing left to add
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_POWER_SENSOR), context={"source": config_entries.SOURCE_USER}
	)
	assert result["type"] is FlowResultType.ABORT
	assert result["reason"] == "no_power_sensors"


async def test_reconfigure_power_sensor_subentry(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	entry = await _setup(hass, ["sensor.a_power"])
	subentry = _subentry(entry, SUBENTRY_POWER_SENSOR, "sensor.a_power")

	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_POWER_SENSOR),
		context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
	)
	assert result["type"] is FlowResultType.FORM
	assert result["step_id"] == "reconfigure"
	assert result["description_placeholders"] == {"source": "sensor.a_power"}

	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"name": "Office", "periods": ["monthly"]}
	)
	assert result["type"] is FlowResultType.ABORT
	assert result["reason"] == "reconfigure_successful"
	await hass.async_block_till_done()

	subentry = entry.subentries[subentry.subentry_id]
	assert subentry.title == "Office"
	assert subentry.data["periods"] == ["monthly"]
	assert hass.states.get("sensor.a_energy").name == "Office Energy"
	assert _entity_id(hass, "a_monthly_energy") is not None
	assert _entity_id(hass, "a_daily_energy") is None
	assert hass.states.get(_entity_id(hass, "a_monthly_energy")).name == "Office Monthly Energy"


async def test_constant_load_subentry_flow(hass: HomeAssistant) -> None:
	hass.states.async_set("switch.heater", "on", {"friendly_name": "Heater"})
	entry = await _setup(hass)

	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_CONSTANT_LOAD), context={"source": config_entries.SOURCE_USER}
	)
	assert result["type"] is FlowResultType.FORM
	# The wattage starts empty; submitting without it asks for one
	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"switch_entity_id": "switch.heater", "periods": []}
	)
	assert result["errors"] == {"power_w": "invalid_power"}

	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"switch_entity_id": "switch.heater", "power_w": 2000, "periods": ["daily"]}
	)
	assert result["type"] is FlowResultType.CREATE_ENTRY
	assert result["title"] == "Heater"
	await hass.async_block_till_done()

	main = _entity_id(hass, "heater_constant_energy")
	assert main is not None
	assert hass.states.get(main).attributes["constant_power_w"] == 2000
	assert _entity_id(hass, "heater_constant_daily_energy") is not None

	# The same switch cannot be added twice
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_CONSTANT_LOAD), context={"source": config_entries.SOURCE_USER}
	)
	assert _schema_field(result, "switch_entity_id").config["exclude_entities"] == ["switch.heater"]
	with pytest.raises(InvalidData):
		await hass.config_entries.subentries.async_configure(
			result["flow_id"], {"switch_entity_id": "switch.heater", "power_w": 10}
		)

	# Reconfigure the wattage and name
	subentry = _subentry(entry, SUBENTRY_CONSTANT_LOAD, "switch.heater")
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_CONSTANT_LOAD),
		context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
	)
	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"power_w": 1500, "name": "Loft Heater", "periods": ["daily"]}
	)
	assert result["type"] is FlowResultType.ABORT
	await hass.async_block_till_done()
	assert entry.subentries[subentry.subentry_id].title == "Loft Heater"
	assert hass.states.get(main).attributes["constant_power_w"] == 1500
	assert hass.states.get(main).name == "Loft Heater Energy"


async def test_price_adjust_subentry_flow(hass: HomeAssistant) -> None:
	hass.states.async_set("sensor.nordpool", "1.5", {"unit_of_measurement": "SEK/kWh", "friendly_name": "Nordpool"})
	entry = await _setup(hass)

	hass.states.async_set("sensor.outdoor_temp", "12", {"unit_of_measurement": "°C"})
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_PRICE_ADJUST), context={"source": config_entries.SOURCE_USER}
	)
	assert result["type"] is FlowResultType.FORM
	# Only price-like sensors are offered
	assert _schema_field(result, "source_entity_id").config["include_entities"] == ["sensor.nordpool"]
	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"source_entity_id": "sensor.nordpool", "add_amount": 0.25}
	)
	assert result["type"] is FlowResultType.CREATE_ENTRY
	assert result["title"] == "Nordpool + 0.25"
	await hass.async_block_till_done()

	subentry = _subentry(entry, SUBENTRY_PRICE_ADJUST, "sensor.nordpool")
	entity_id = _entity_id(hass, f"price_adjust_{subentry.data['id']}")
	assert entity_id is not None
	state = hass.states.get(entity_id)
	assert float(state.state) == pytest.approx(1.75)
	assert state.attributes["unit_of_measurement"] == "SEK/kWh"

	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_PRICE_ADJUST),
		context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
	)
	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"add_amount": -0.5, "name": "Spot minus support"}
	)
	assert result["type"] is FlowResultType.ABORT
	await hass.async_block_till_done()
	assert entry.subentries[subentry.subentry_id].title == "Spot minus support"
	assert float(hass.states.get(entity_id).state) == pytest.approx(1.0)


async def test_settings_options_flow(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	entry = await _setup(hass, ["sensor.a_power"], options={"max_energy_per_hour": 7})
	assert _entity_id(hass, "synthetic_grid_total_energy") is None

	result = await hass.config_entries.options.async_init(entry.entry_id)
	assert result["type"] is FlowResultType.FORM
	assert result["step_id"] == "init"

	result = await hass.config_entries.options.async_configure(
		result["flow_id"], {"sample_interval": 30, "create_synthetic_grid_total": True, "debug_logging": False}
	)
	assert result["type"] is FlowResultType.CREATE_ENTRY
	await hass.async_block_till_done()

	assert entry.options["sample_interval"] == 30
	assert entry.options["create_synthetic_grid_total"] is True
	assert entry.options["max_energy_per_hour"] == 7
	assert entry.state is ConfigEntryState.LOADED
	assert _entity_id(hass, "synthetic_grid_total_energy") is not None
	assert hass.states.get("sensor.a_energy").attributes["sample_interval"] == 30


async def test_price_picker_falls_back_to_all_sensors(hass: HomeAssistant) -> None:
	hass.states.async_set("sensor.custom_price", "1.1", {"unit_of_measurement": "SEK"})
	entry = await _setup(hass)
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_PRICE_ADJUST), context={"source": config_entries.SOURCE_USER}
	)
	picker = _schema_field(result, "source_entity_id")
	assert "include_entities" not in picker.config
	assert picker.config["domain"] == ["sensor"]


async def test_empty_legacy_main_device_is_removed(hass: HomeAssistant) -> None:
	from homeassistant.helpers import device_registry as dr

	_power(hass, "sensor.a_power", 0, "A Power")
	entry = MockConfigEntry(
		domain=DOMAIN, unique_id=DOMAIN, version=2, data={}, options={},
		subentries_data=[_power_subentry("sensor.a_power")],
	)
	entry.add_to_hass(hass)
	device_registry = dr.async_get(hass)
	device_registry.async_get_or_create(
		config_entry_id=entry.entry_id, identifiers={(DOMAIN, "main")}, name="Energy Sensor Generator"
	)
	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()

	assert not [d for d in dr.async_entries_for_config_entry(device_registry, entry.entry_id) if (DOMAIN, "main") in d.identifiers]
	assert _entity_id(hass, "a_energy") is not None


async def test_sources_without_a_device_get_a_readable_device_name(hass: HomeAssistant) -> None:
	from homeassistant.helpers import device_registry as dr

	hass.states.async_set("switch.boilercontrol_boiler_relay_1", "off", {"friendly_name": "Relay 1"})
	entry = await _setup(hass, subentries=[ConfigSubentryData(
		subentry_type=SUBENTRY_CONSTANT_LOAD,
		title="BoilerControl",
		unique_id=subentry_unique_id(SUBENTRY_CONSTANT_LOAD, "switch.boilercontrol_boiler_relay_1"),
		data={"switch_entity_id": "switch.boilercontrol_boiler_relay_1", "power_w": 3000, "name": "BoilerControl", "periods": []},
	)])
	subentry = _subentry(entry, SUBENTRY_CONSTANT_LOAD, "switch.boilercontrol_boiler_relay_1")
	device = next(
		d for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
		if (DOMAIN, f"source_{subentry.subentry_id}") in d.identifiers
	)
	assert device.name == "BoilerControl"
	assert device.model == "Constant load"


async def test_sources_sharing_a_meter_device_keep_their_entities(hass: HomeAssistant, caplog) -> None:
	"""Three phases of one meter used to share the meter's device.

	Home Assistant 2026.10 lets a device belong to one subentry only, so
	sharing it moved the device between subentries and deleted the previous
	phases' registry entries. Each source now has a device of its own.
	"""
	from homeassistant.helpers import area_registry as ar
	from homeassistant.helpers import device_registry as dr

	meter_entry = MockConfigEntry(domain="esphome", title="P1 meter")
	meter_entry.add_to_hass(hass)
	area = ar.async_get(hass).async_create("Utility room")
	meter = dr.async_get(hass).async_get_or_create(
		config_entry_id=meter_entry.entry_id, identifiers={("esphome", "p1ib")}, name="P1ib"
	)
	dr.async_get(hass).async_update_device(meter.id, area_id=area.id)
	registry = er.async_get(hass)
	for phase in ("l1", "l2", "l3"):
		registry.async_get_or_create(
			"sensor", "esphome", f"p1ib_{phase}", suggested_object_id=f"p1ib_power_{phase}",
			config_entry=meter_entry, device_id=meter.id,
		)
		_power(hass, f"sensor.p1ib_power_{phase}", 100, f"P1ib Power {phase.upper()}")

	# Version 1 entry whose entities sit on (a copy of) the meter device
	entry = MockConfigEntry(
		domain=DOMAIN, unique_id=DOMAIN, version=1, data={},
		options={
			"selected_power_sensors": ["sensor.p1ib_power_l1", "sensor.p1ib_power_l2", "sensor.p1ib_power_l3"],
			"create_daily_sensors": False, "create_weekly_sensors": True,
			"create_monthly_sensors": False, "create_annual_sensors": False,
		},
	)
	entry.add_to_hass(hass)
	old_device = dr.async_get(hass).async_get_or_create(
		config_entry_id=entry.entry_id, identifiers={("esphome", "p1ib")}, name="P1ib"
	)
	for phase in ("l1", "l2", "l3"):
		registry.async_get_or_create(
			"sensor", DOMAIN, f"p1ib_{phase}_energy", suggested_object_id=f"meter_{phase}_total",
			config_entry=entry, device_id=old_device.id,
		)

	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()

	for phase in ("l1", "l2", "l3"):
		reg_entry = registry.async_get(f"sensor.meter_{phase}_total")
		assert reg_entry is not None, phase  # entity ID kept, so Energy dashboard history is kept
		assert reg_entry.config_subentry_id == _subentry(entry, SUBENTRY_POWER_SENSOR, f"sensor.p1ib_power_{phase}").subentry_id
		assert _entity_id(hass, f"p1ib_{phase}_weekly_energy") is not None
		device = dr.async_get(hass).async_get(reg_entry.device_id)
		assert device.area_id == area.id
	assert len({registry.async_get(f"sensor.meter_{p}_total").device_id for p in ("l1", "l2", "l3")}) == 3
	# The meter's own entities and device are untouched; our old copy is gone
	assert registry.async_get("sensor.p1ib_power_l1").device_id == meter.id
	assert dr.async_get(hass).async_get(meter.id) is not None
	ours = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
	assert all(("esphome", "p1ib") not in device.identifiers for device in ours)
	assert "different config subentry" not in caplog.text
	assert "deprecated" not in caplog.text.lower()


async def test_changing_one_source_leaves_the_others_running(hass: HomeAssistant) -> None:
	"""Adding, editing or deleting a source used to reload the whole entry.

	Every sensor went unavailable for a moment, so helpers summing them dipped
	and the Energy dashboard counted a reset plus a spike.
	"""
	from homeassistant.core import callback

	_power(hass, "sensor.a_power", 0, "A Power")
	_power(hass, "sensor.b_power", 0, "B Power")
	_power(hass, "sensor.c_power", 0, "C Power")
	entry = await _setup(hass, ["sensor.a_power", "sensor.b_power"])
	live = hass.data[DOMAIN][entry.entry_id]["entities"]
	a_main, a_daily = live["a_energy"], live["a_daily_energy"]
	await hass.services.async_call(DOMAIN, "adjust_energy", {"entity_id": "sensor.a_energy", "set_to_value": 5}, blocking=True)

	a_states: list[str] = []

	@callback
	def watch(event) -> None:
		if event.data["entity_id"].startswith("sensor.a_") and event.data["new_state"] is not None:
			a_states.append(event.data["new_state"].state)
		elif event.data["entity_id"].startswith("sensor.a_"):
			a_states.append("removed")

	hass.bus.async_listen("state_changed", watch)

	# Add a source
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_POWER_SENSOR), context={"source": config_entries.SOURCE_USER}
	)
	await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"source_entity_id": "sensor.c_power", "periods": ["daily"]}
	)
	await hass.async_block_till_done()
	assert _entity_id(hass, "c_energy") is not None
	assert hass.states.get(_entity_id(hass, "c_energy")) is not None

	# Edit another source's periods and name
	b = _subentry(entry, SUBENTRY_POWER_SENSOR, "sensor.b_power")
	hass.config_entries.async_update_subentry(entry, b, title="Bee", data={**b.data, "periods": ["weekly"], "name": "Bee"})
	await hass.async_block_till_done()
	assert _entity_id(hass, "b_weekly_energy") is not None
	assert _entity_id(hass, "b_daily_energy") is None
	assert hass.states.get(_entity_id(hass, "b_energy")).name == "Bee Energy"

	# Delete the new source
	hass.config_entries.async_remove_subentry(entry, _subentry(entry, SUBENTRY_POWER_SENSOR, "sensor.c_power").subentry_id)
	await hass.async_block_till_done()
	assert _entity_id(hass, "c_energy") is None
	assert "c_energy" not in hass.data[DOMAIN][entry.entry_id]["entities"]

	# Source A was never touched: same objects, never unavailable or removed
	assert live["a_energy"] is a_main and live["a_daily_energy"] is a_daily
	assert all(state not in ("unavailable", "unknown", "removed") for state in a_states), a_states
	assert float(hass.states.get("sensor.a_energy").state) == pytest.approx(5)
	assert entry.state is ConfigEntryState.LOADED


async def test_settings_change_still_reloads_everything(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	entry = await _setup(hass, ["sensor.a_power"])
	before = hass.data[DOMAIN][entry.entry_id]["entities"]["a_energy"]

	hass.config_entries.async_update_entry(entry, options={**entry.options, "sample_interval": 30})
	await hass.async_block_till_done()

	after = hass.data[DOMAIN][entry.entry_id]["entities"]["a_energy"]
	assert after is not before
	assert after.extra_state_attributes["sample_interval"] == 30
