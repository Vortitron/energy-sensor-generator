"""Built-in sources: a core Integral helper makes the kWh total, this integration adds to it."""
from __future__ import annotations

import pytest

from custom_components.energy_sensor_generator.naming import (
	SUBENTRY_CONSTANT_LOAD,
	SUBENTRY_POWER_SENSOR,
	desired_unique_ids,
	integral_flow_input,
	integral_units,
	is_main_energy_unique_id,
	plan_sources,
)
from custom_components.energy_sensor_generator.utils import derive_constant_base_name


class _Sub:
	def __init__(self, subentry_type, data, subentry_id="s1", title="T"):
		self.subentry_type = subentry_type
		self.data = data
		self.subentry_id = subentry_id
		self.title = title


# ---------------------------------------------------------------------------
# Pure planning
# ---------------------------------------------------------------------------

def test_integral_units_match_the_energy_dashboard() -> None:
	assert integral_units("W") == {"unit_prefix": "k", "unit_time": "h"}
	assert integral_units("kW") == {"unit_time": "h"}
	assert integral_units("MW") == {"unit_time": "h"}
	assert integral_units("kWh") is None
	assert integral_units(None) is None


def test_integral_flow_input_uses_the_dashboard_settings() -> None:
	assert integral_flow_input("Kettle energy", "sensor.kettle_power", "W") == {
		"name": "Kettle energy",
		"source": "sensor.kettle_power",
		"method": "left",
		"max_sub_interval": {"hours": 0, "minutes": 5, "seconds": 0},
		"round": 3,
		"unit_prefix": "k",
		"unit_time": "h",
	}
	assert integral_flow_input("X", "sensor.x", "%") is None


def test_sources_without_a_calculation_stay_on_the_own_integrator() -> None:
	(plan,) = plan_sources([_Sub(SUBENTRY_POWER_SENSOR, {"source_entity_id": "sensor.a_power"})], derive_constant_base_name)
	assert plan.calculation == "esg" and not plan.builtin
	assert desired_unique_ids([plan]) >= {"a_energy", "a_daily_energy"}


def test_builtin_sources_want_no_main_sensor() -> None:
	plans = plan_sources(
		[
			_Sub(SUBENTRY_POWER_SENSOR, {"source_entity_id": "sensor.a_power", "calculation": "builtin", "periods": ["daily"]}, "s1"),
			_Sub(
				SUBENTRY_CONSTANT_LOAD,
				{"switch_entity_id": "switch.heater", "power_w": 2000, "calculation": "builtin", "periods": []},
				"s2",
			),
		],
		derive_constant_base_name,
	)
	assert [plan.builtin for plan in plans] == [True, True]
	assert desired_unique_ids(plans) == {"a_daily_energy", "heater_constant_power"}
	# The constant load's power sensor is not a kWh total
	assert not is_main_energy_unique_id("heater_constant_power")


# ---------------------------------------------------------------------------
# Against Home Assistant
# ---------------------------------------------------------------------------

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.config_entries import ConfigSubentryData  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.energy_sensor_generator.const import DOMAIN  # noqa: E402
from custom_components.energy_sensor_generator.naming import subentry_unique_id  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
	yield


@pytest.fixture
def expected_lingering_timers() -> bool:
	return True


def _power(hass: HomeAssistant, entity_id: str, value: float, name: str, unit: str = "W") -> None:
	hass.states.async_set(entity_id, str(value), {"unit_of_measurement": unit, "device_class": "power", "friendly_name": name})


def _builtin(entity_id: str, periods: list[str] | None = None) -> ConfigSubentryData:
	return ConfigSubentryData(
		subentry_type=SUBENTRY_POWER_SENSOR,
		title=entity_id.split(".")[1].replace("_power", "").title(),
		unique_id=subentry_unique_id(SUBENTRY_POWER_SENSOR, entity_id),
		data={"source_entity_id": entity_id, "periods": periods or ["daily"], "calculation": "builtin"},
	)


async def _setup(hass: HomeAssistant, subentries: list[ConfigSubentryData]) -> MockConfigEntry:
	entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, version=2, data={}, options={}, subentries_data=subentries)
	entry.add_to_hass(hass)
	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()
	return entry


async def _create_integral(hass: HomeAssistant, source: str, name: str) -> str:
	"""Create an Integral helper the way the Energy dashboard does."""
	result = await hass.config_entries.flow.async_init("integration", context={"source": config_entries.SOURCE_USER})
	result = await hass.config_entries.flow.async_configure(
		result["flow_id"],
		{
			"name": name,
			"source": source,
			"method": "left",
			"round": 3,
			"unit_prefix": "k",
			"unit_time": "h",
			"max_sub_interval": {"hours": 0, "minutes": 5, "seconds": 0},
		},
	)
	assert result["type"] is FlowResultType.CREATE_ENTRY
	await hass.async_block_till_done()
	return result["result"].entry_id


def _integrals(hass: HomeAssistant, source: str) -> list:
	return [e for e in hass.config_entries.async_entries("integration") if e.options.get("source") == source]


def _sensor_of(hass: HomeAssistant, config_entry_id: str) -> str:
	(reg,) = [r for r in er.async_entries_for_config_entry(er.async_get(hass), config_entry_id) if r.domain == "sensor"]
	return reg.entity_id


def _only_subentry(entry: MockConfigEntry):
	(subentry,) = entry.subentries.values()
	return subentry


async def test_builtin_source_creates_an_owned_integral(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, [_builtin("sensor.kettle_power")])

	(integral_entry,) = _integrals(hass, "sensor.kettle_power")
	assert integral_entry.title == "Kettle energy"
	assert integral_entry.options["method"] == "left"
	integral = _sensor_of(hass, integral_entry.entry_id)
	assert hass.states.get(integral).attributes["unit_of_measurement"] == "kWh"

	data = _only_subentry(entry).data
	assert data["integral_entry_id"] == integral_entry.entry_id
	assert data["integral_entity_id"] == integral
	assert data["integral_owned"] is True
	# No own kWh sensor; the period total sits on top of the helper
	assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "kettle_energy") is None
	assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "kettle_daily_energy") is not None


async def test_builtin_source_reuses_an_existing_integral(hass: HomeAssistant) -> None:
	"""A helper the Energy dashboard (or the user) made is reused, not duplicated."""
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	existing = await _create_integral(hass, "sensor.kettle_power", "Kettle from dashboard")

	entry = await _setup(hass, [_builtin("sensor.kettle_power")])

	assert [e.entry_id for e in _integrals(hass, "sensor.kettle_power")] == [existing]
	data = _only_subentry(entry).data
	assert data["integral_entry_id"] == existing
	assert data["integral_owned"] is False

	# Deleting the source leaves the user's helper alone
	hass.config_entries.async_remove_subentry(entry, _only_subentry(entry).subentry_id)
	await hass.async_block_till_done()
	assert hass.config_entries.async_get_entry(existing) is not None


async def test_deleting_a_source_removes_the_helper_it_created(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, [_builtin("sensor.kettle_power")])
	(integral_entry,) = _integrals(hass, "sensor.kettle_power")

	hass.config_entries.async_remove_subentry(entry, _only_subentry(entry).subentry_id)
	await hass.async_block_till_done()

	assert hass.config_entries.async_get_entry(integral_entry.entry_id) is None


async def test_a_helper_on_the_energy_dashboard_is_kept(hass: HomeAssistant) -> None:
	from homeassistant.components.energy.data import async_get_manager

	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, [_builtin("sensor.kettle_power")])
	(integral_entry,) = _integrals(hass, "sensor.kettle_power")
	integral = _sensor_of(hass, integral_entry.entry_id)

	manager = await async_get_manager(hass)
	await manager.async_update({"device_consumption": [{"stat_consumption": integral}]})

	hass.config_entries.async_remove_subentry(entry, _only_subentry(entry).subentry_id)
	await hass.async_block_till_done()

	assert hass.config_entries.async_get_entry(integral_entry.entry_id) is not None


async def test_period_total_follows_the_helper(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, [_builtin("sensor.kettle_power")])
	integral = _only_subentry(entry).data["integral_entity_id"]
	daily = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "kettle_daily_energy")

	for value in ("1.0", "3.5", "4.0"):
		hass.states.async_set(integral, value, {"unit_of_measurement": "kWh", "device_class": "energy"})
		await hass.async_block_till_done()

	# Counted from the first reading after it was linked
	assert float(hass.states.get(daily).state) == pytest.approx(3.0)


async def test_builtin_constant_load_publishes_power_for_the_helper(hass: HomeAssistant) -> None:
	hass.states.async_set("switch.heater", "off", {"friendly_name": "Heater"})
	entry = await _setup(hass, [])

	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_CONSTANT_LOAD), context={"source": config_entries.SOURCE_USER}
	)
	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"switch_entity_id": "switch.heater", "power_w": 2000, "periods": ["daily"]}
	)
	assert result["type"] is FlowResultType.CREATE_ENTRY
	await hass.async_block_till_done()

	power = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "heater_constant_power")
	assert power is not None
	assert float(hass.states.get(power).state) == 0
	assert hass.states.get(power).attributes["device_class"] == "power"

	hass.states.async_set("switch.heater", "on", {"friendly_name": "Heater"})
	await hass.async_block_till_done()
	assert float(hass.states.get(power).state) == 2000

	hass.states.async_set("switch.heater", "unavailable")
	await hass.async_block_till_done()
	assert hass.states.get(power).state == "unavailable"

	# The helper integrates that power sensor, and the energy main sensor is gone
	assert len(_integrals(hass, power)) == 1
	assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "heater_constant_energy") is None
	assert _only_subentry(entry).data["integral_owned"] is True


async def test_own_power_sensors_are_not_offered_as_sources(hass: HomeAssistant) -> None:
	hass.states.async_set("switch.heater", "on", {"friendly_name": "Heater"})
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, [])
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_CONSTANT_LOAD), context={"source": config_entries.SOURCE_USER}
	)
	await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"switch_entity_id": "switch.heater", "power_w": 500, "periods": []}
	)
	await hass.async_block_till_done()

	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_POWER_SENSOR), context={"source": config_entries.SOURCE_USER}
	)
	picker = next(v for k, v in result["data_schema"].schema.items() if str(k) == "source_entity_id")
	assert picker.config["include_entities"] == ["sensor.kettle_power"]


async def test_protected_options_are_refused_for_builtin(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, [])
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_POWER_SENSOR), context={"source": config_entries.SOURCE_USER}
	)
	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"source_entity_id": "sensor.kettle_power", "max_power_w": 5000}
	)
	assert result["errors"] == {"max_power_w": "protected_only"}


async def test_builtin_edit_form_has_no_protected_fields(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = await _setup(hass, [_builtin("sensor.kettle_power")])
	subentry = _only_subentry(entry)
	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_POWER_SENSOR),
		context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
	)
	assert result["step_id"] == "reconfigure_builtin"
	assert {str(key) for key in result["data_schema"].schema} == {"source_entity_id", "name", "periods"}

	result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": "Tea", "periods": ["weekly"]})
	assert result["type"] is FlowResultType.ABORT
	await hass.async_block_till_done()
	data = entry.subentries[subentry.subentry_id].data
	assert data["periods"] == ["weekly"]
	# The link to the helper survives the edit
	assert data["integral_owned"] is True and data["integral_entry_id"]
	assert len(_integrals(hass, "sensor.kettle_power")) == 1


async def test_existing_sources_are_left_on_the_own_integrator(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	await _setup(
		hass,
		[
			ConfigSubentryData(
				subentry_type=SUBENTRY_POWER_SENSOR,
				title="Kettle",
				unique_id=subentry_unique_id(SUBENTRY_POWER_SENSOR, "sensor.kettle_power"),
				data={"source_entity_id": "sensor.kettle_power", "periods": ["daily"]},
			)
		],
	)
	assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "kettle_energy") is not None
	assert hass.config_entries.async_entries("integration") == []


async def test_synthetic_grid_total_includes_builtin_helpers(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	entry = MockConfigEntry(
		domain=DOMAIN,
		unique_id=DOMAIN,
		version=2,
		data={},
		options={"create_synthetic_grid_total": True},
		subentries_data=[_builtin("sensor.kettle_power")],
	)
	entry.add_to_hass(hass)
	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()
	integral = _only_subentry(entry).data["integral_entity_id"]
	live = hass.data[DOMAIN][entry.entry_id]["entities"]["synthetic_grid_total_energy"]
	assert integral in live._find_sources()


async def test_a_builtin_source_moved_to_a_new_plug_repoints_its_helper(hass: HomeAssistant) -> None:
	_power(hass, "sensor.kettle_power", 0, "Kettle Power")
	_power(hass, "sensor.new_kettle_power", 1800, "New kettle Power")
	entry = await _setup(hass, [_builtin("sensor.kettle_power")])
	subentry = _only_subentry(entry)
	(integral,) = _integrals(hass, "sensor.kettle_power")
	counter = _sensor_of(hass, integral.entry_id)
	daily = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "kettle_daily_energy")

	result = await hass.config_entries.subentries.async_init(
		(entry.entry_id, SUBENTRY_POWER_SENSOR),
		context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
	)
	result = await hass.config_entries.subentries.async_configure(
		result["flow_id"], {"source_entity_id": "sensor.new_kettle_power", "periods": ["daily"]}
	)
	assert result["reason"] == "reconfigure_successful"
	await hass.async_block_till_done()

	# The same helper, now integrating the new plug; nothing new made
	assert _integrals(hass, "sensor.kettle_power") == []
	(moved,) = _integrals(hass, "sensor.new_kettle_power")
	assert moved.entry_id == integral.entry_id
	assert _sensor_of(hass, moved.entry_id) == counter
	assert len(hass.config_entries.async_entries("integration")) == 1
	assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "kettle_daily_energy") == daily
	assert entry.subentries[subentry.subentry_id].data["integral_entry_id"] == integral.entry_id
