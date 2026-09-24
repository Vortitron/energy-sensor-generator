"""End-to-end tests against a real Home Assistant core.

Requires pytest-homeassistant-custom-component; skipped when it is not installed.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_sensor_generator.const import DOMAIN

STORAGE_KEY = "energy_sensor_generator"


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


async def _setup(hass: HomeAssistant, options: dict) -> MockConfigEntry:
	entry = MockConfigEntry(
		domain=DOMAIN,
		unique_id=DOMAIN,
		data={"sample_interval": 60},
		options=options,
	)
	entry.add_to_hass(hass)
	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()
	return entry


def _entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
	return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)


async def test_creates_main_and_period_sensors(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kitchen Kettle Power")
	_power(hass, "sensor.plug_power_2", 0, "Plug Two Power")

	await _setup(hass, {
		"selected_power_sensors": ["sensor.kettle_power", "sensor.plug_power_2"],
		"create_daily_sensors": True,
		"create_weekly_sensors": False,
		"create_monthly_sensors": False,
		"create_annual_sensors": False,
	})

	# Entity IDs come from the friendly name, unique IDs from the source entity
	assert _entity_id(hass, "kettle_energy") == "sensor.kitchen_kettle_energy"
	assert _entity_id(hass, "kettle_daily_energy") == "sensor.kitchen_kettle_daily_energy"
	# <root>_power_<n> is disambiguated to <root>_energy_<n>
	assert _entity_id(hass, "plug_energy_2") is not None
	assert _entity_id(hass, "plug_energy_2_daily_energy") is not None
	assert _entity_id(hass, "kettle_weekly_energy") is None


async def test_period_sensor_follows_name_based_main_entity(hass: HomeAssistant) -> None:
	"""Periods used to track sensor.<base>_energy, which does not exist when HA names the entity after its friendly name."""
	_power(hass, "sensor.kettle_power", 0, "Kitchen Kettle Power")
	await _setup(hass, {"selected_power_sensors": ["sensor.kettle_power"]})

	await hass.services.async_call(
		DOMAIN, "adjust_energy",
		{"entity_id": "sensor.kitchen_kettle_energy", "adjustment_kwh": 1.5},
		blocking=True,
	)
	await hass.async_block_till_done()
	# First reading anchors the period sensor; the next increase is counted
	await hass.services.async_call(
		DOMAIN, "adjust_energy",
		{"entity_id": "sensor.kitchen_kettle_energy", "adjustment_kwh": 2.0},
		blocking=True,
	)
	await hass.async_block_till_done()

	assert float(hass.states.get("sensor.kitchen_kettle_energy").state) == pytest.approx(3.5)
	assert float(hass.states.get("sensor.kitchen_kettle_daily_energy").state) == pytest.approx(2.0)

	# Renaming the main entity must not break tracking
	er.async_get(hass).async_update_entity("sensor.kitchen_kettle_energy", new_entity_id="sensor.kettle_total")
	await hass.async_block_till_done()
	await hass.services.async_call(
		DOMAIN, "adjust_energy",
		{"entity_id": "sensor.kettle_total", "adjustment_kwh": 1.0},
		blocking=True,
	)
	await hass.async_block_till_done()
	assert float(hass.states.get("sensor.kitchen_kettle_daily_energy").state) == pytest.approx(3.0)


async def test_adjust_energy_survives_the_next_save(hass: HomeAssistant, hass_storage) -> None:
	"""Services used to edit storage directly and the live entity overwrote it on its next save."""
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, {"selected_power_sensors": ["sensor.kettle_power"]})

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
	await _setup(hass, {
		"selected_power_sensors": ["sensor.a_power", "sensor.b_power"],
		"create_daily_sensors": False,
		"create_weekly_sensors": False,
		"create_monthly_sensors": False,
		"create_annual_sensors": False,
	})
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
	await _setup(hass, {"selected_power_sensors": ["sensor.kettle_power"]})

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


async def test_options_change_reloads_and_removes_deselected(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	_power(hass, "sensor.b_power", 0, "B Power")
	entry = await _setup(hass, {"selected_power_sensors": ["sensor.a_power", "sensor.b_power"]})
	assert _entity_id(hass, "b_energy") is not None
	assert _entity_id(hass, "b_annual_energy") is not None

	hass.config_entries.async_update_entry(
		entry, options={**entry.options, "selected_power_sensors": ["sensor.a_power"], "create_annual_sensors": False}
	)
	await hass.async_block_till_done()

	assert entry.state is ConfigEntryState.LOADED
	assert _entity_id(hass, "a_energy") is not None
	assert _entity_id(hass, "a_daily_energy") is not None
	assert _entity_id(hass, "a_annual_energy") is None
	assert _entity_id(hass, "b_energy") is None
	assert hass.states.get("sensor.a_energy") is not None


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
	await _setup(hass, {"selected_power_sensors": ["sensor.kettle_power"]})

	assert float(hass.states.get("sensor.kettle_energy").state) == pytest.approx(40)
	assert float(hass.states.get("sensor.kettle_daily_energy").state) == 0
	assert float(hass.states.get("sensor.kettle_annual_energy").state) == pytest.approx(30)


async def test_synthetic_grid_total_sums_main_sensors(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	_power(hass, "sensor.b_power_2", 0, "B Power")
	await _setup(hass, {
		"selected_power_sensors": ["sensor.a_power", "sensor.b_power_2"],
		"create_synthetic_grid_total": True,
	})
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
	entry = await _setup(hass, {"selected_power_sensors": ["sensor.kettle_power"]})

	await hass.services.async_call(DOMAIN, "generate_sensors", {}, blocking=True)
	await hass.async_block_till_done()
	assert entry.state is ConfigEntryState.LOADED
	assert hass.states.get("sensor.kettle_energy") is not None


async def test_options_flow_keeps_unavailable_selection(hass: HomeAssistant) -> None:
	_power(hass, "sensor.a_power", 0, "A Power")
	entry = await _setup(hass, {"selected_power_sensors": ["sensor.a_power", "sensor.gone_power"]})

	result = await hass.config_entries.options.async_init(entry.entry_id)
	assert result["type"] is FlowResultType.MENU
	assert result["description_placeholders"]["pending"] == ""

	result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "sensors"})
	assert result["type"] is FlowResultType.FORM
	selector = next(
		value for key, value in result["data_schema"].schema.items() if str(key) == "selected_power_sensors"
	)
	labels = {option["value"]: option["label"] for option in selector.config["options"]}
	assert labels["sensor.gone_power"].endswith("(unavailable)")
	assert not labels["sensor.a_power"].endswith("(unavailable)")

	# Submitting the page unchanged must keep the offline sensor
	result = await hass.config_entries.options.async_configure(
		result["flow_id"],
		{"selected_power_sensors": ["sensor.a_power", "sensor.gone_power"], "period_sensors": ["daily"]},
	)
	assert result["type"] is FlowResultType.MENU
	assert result["description_placeholders"]["pending"] != ""

	result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "save"})
	assert result["type"] is FlowResultType.CREATE_ENTRY
	await hass.async_block_till_done()
	assert entry.options["selected_power_sensors"] == ["sensor.a_power", "sensor.gone_power"]
	assert entry.options["create_weekly_sensors"] is False
	assert _entity_id(hass, "a_weekly_energy") is None
	assert _entity_id(hass, "a_daily_energy") is not None


async def test_options_flow_blank_add_returns_to_menu(hass: HomeAssistant) -> None:
	entry = await _setup(hass, {})
	result = await hass.config_entries.options.async_init(entry.entry_id)
	result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "constant_devices"})
	result = await hass.config_entries.options.async_configure(result["flow_id"], {"constant_device_action": "add"})
	assert result["type"] is FlowResultType.MENU
	assert result.get("errors") in (None, {})
