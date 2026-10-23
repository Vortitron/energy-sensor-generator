"""Implausible readings, a stalled recorder and a total that must not go backwards.

Numbers from sensor.sam_current_energy on GamlaBio (sensor.sam_current_power,
panasonic_cc, cloud polled about every 15 minutes), October 2026.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.energy_sensor_generator.const import DOMAIN
from custom_components.energy_sensor_generator.naming import SUBENTRY_POWER_SENSOR, subentry_unique_id

SOURCE = "sensor.sam_current_power"
STORAGE_KEY = "energy_sensor_generator"


@pytest.fixture
def expected_lingering_timers() -> bool:
	return True


def _power(hass: HomeAssistant, watts: float) -> None:
	hass.states.async_set(
		SOURCE, str(watts), {"unit_of_measurement": "W", "device_class": "power", "friendly_name": "Sam Current Power"}
	)


async def _setup(hass: HomeAssistant, options: dict | None = None, max_power_w: float | None = None) -> MockConfigEntry:
	data = {"source_entity_id": SOURCE, "periods": []}
	if max_power_w is not None:
		data["max_power_w"] = max_power_w
	entry = MockConfigEntry(
		domain=DOMAIN, unique_id=DOMAIN, version=2, data={}, options=options or {},
		subentries_data=[{
			"subentry_type": SUBENTRY_POWER_SENSOR, "title": "Sam",
			"unique_id": subentry_unique_id(SUBENTRY_POWER_SENSOR, SOURCE), "data": data,
		}],
	)
	entry.add_to_hass(hass)
	assert await hass.config_entries.async_setup(entry.entry_id)
	await hass.async_block_till_done()
	return entry


def _sensor(hass: HomeAssistant, entry: MockConfigEntry):
	return hass.data[DOMAIN][entry.entry_id]["entities"]["sam_current_energy"]


def _at(text: str) -> datetime:
	return dt_util.parse_datetime(f"2026-10-22T{text}+00:00")


async def _run_glitch(hass: HomeAssistant, freezer, entry: MockConfigEntry) -> float:
	"""13:00-13:36 at 720 W, with 183051 W reported from 13:20:31 to 13:35:09."""
	sensor = _sensor(hass, entry)
	start = sensor.total
	changes = {"13:20:31": 183051, "13:35:09": 720}
	minute = _at("13:01:00")
	while minute <= _at("13:36:00"):
		for when, watts in changes.items():
			if _at(when) <= minute and (minute - _at(when)).total_seconds() < 60:
				freezer.move_to(_at(when))
				_power(hass, watts)
				await hass.async_block_till_done()
		await async_wait_recording_done(hass)
		freezer.move_to(minute)
		await sensor._handle_interval_update(minute)
		minute = minute.replace(minute=minute.minute + 1) if minute.minute < 59 else minute
		if minute.minute == 0:
			break
	return sensor.total - start


async def _prime(hass: HomeAssistant, freezer, options: dict | None = None, max_power_w: float | None = None):
	# The reading predates the first window, as a real one always does
	freezer.move_to(_at("12:59:00"))
	_power(hass, 720)
	entry = await _setup(hass, options, max_power_w)
	await async_wait_recording_done(hass)
	freezer.move_to(_at("13:00:00"))
	sensor = _sensor(hass, entry)
	sensor._last_statistical_calculation = _at("13:00:00")
	sensor._last_update = _at("13:00:00")
	sensor._last_power = 720.0
	sensor._using_statistical = True
	return entry


async def test_statistical_ignores_an_impossible_reading(recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer) -> None:
	entry = await _prime(hass, freezer)
	added = await _run_glitch(hass, freezer, entry)

	# 36 minutes at 720 W, not +44.65 kWh
	assert added == pytest.approx(0.72 * 36 / 60, rel=0.02)
	sensor = _sensor(hass, entry)
	assert sensor.extra_state_attributes["rejected_readings"] >= 1


async def test_without_a_limit_the_glitch_is_integrated(recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer) -> None:
	"""The same run with the check turned off reproduces what GamlaBio recorded."""
	entry = await _prime(hass, freezer, options={"max_power_w": 0})
	added = await _run_glitch(hass, freezer, entry)
	assert added > 40


async def test_a_source_limit_overrides_the_global_one(recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer) -> None:
	# 3 kW limit for the heat pump: a 5 kW reading is a glitch here, though below 50 kW
	entry = await _prime(hass, freezer, max_power_w=3000)
	sensor = _sensor(hass, entry)
	freezer.move_to(_at("13:00:30"))
	_power(hass, 5000)
	await hass.async_block_till_done()
	await async_wait_recording_done(hass)
	freezer.move_to(_at("13:01:00"))
	await sensor._handle_interval_update(_at("13:01:00"))
	assert sensor.total == pytest.approx(0.72 / 60, rel=0.02)
	assert sensor.extra_state_attributes["max_power_w"] == 3000


async def test_point_sampling_ignores_an_impossible_reading(recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer) -> None:
	entry = await _prime(hass, freezer, options={"use_statistical_calculation": False})
	sensor = _sensor(hass, entry)
	sensor._using_statistical = False
	added = await _run_glitch(hass, freezer, entry)
	assert added == pytest.approx(0.72 * 36 / 60, rel=0.02)


async def test_stalled_recorder_uses_live_readings(recorder_mock, enable_custom_integrations, hass: HomeAssistant, freezer) -> None:
	"""13 Oct: the recorder wrote nothing from 03:39 to 13:17 while Home Assistant ran.

	History kept returning the last recorded reading; the live power had moved on.
	"""
	entry = await _prime(hass, freezer)
	sensor = _sensor(hass, entry)
	stale = {SOURCE: [State(SOURCE, "1211", last_updated=_at("03:35:49"), last_changed=_at("03:35:49"))]}

	with patch("homeassistant.components.recorder.history.get_significant_states", return_value=stale):
		freezer.move_to(_at("13:00:20"))
		_power(hass, 2000)
		await hass.async_block_till_done()
		freezer.move_to(_at("13:01:00"))
		await sensor._handle_interval_update(_at("13:01:00"))
		first_minute = sensor.total
		freezer.move_to(_at("13:02:00"))
		await sensor._handle_interval_update(_at("13:02:00"))

	# 20 s at 720 W then 40 s at 2000 W, then a minute at 2000 W: live values, not 1211 W
	assert first_minute == pytest.approx((0.72 * 20 + 2.0 * 40) / 3600, rel=0.02)
	assert sensor.total == pytest.approx(first_minute + 2.0 / 60, rel=0.02)
	assert sensor._last_statistical_calculation == _at("13:02:00")


async def test_total_never_restores_backwards(enable_custom_integrations, hass: HomeAssistant, hass_storage) -> None:
	"""5 Oct: the total dropped 43.95 kWh after storage fell behind the recorded state."""
	hass_storage[STORAGE_KEY] = {
		"version": 1, "minor_version": 1, "key": STORAGE_KEY,
		"data": {"sam_current_energy": {"value": 4700.0}},
	}
	mock_restore_cache(hass, [State("sensor.sam_current_energy", "4743.95")])
	_power(hass, 720)
	entry = await _setup(hass)
	assert _sensor(hass, entry).total == pytest.approx(4743.95)
	assert float(hass.states.get("sensor.sam_current_energy").state) == pytest.approx(4743.95)


async def test_lower_restored_state_does_not_override_storage(enable_custom_integrations, hass: HomeAssistant, hass_storage) -> None:
	hass_storage[STORAGE_KEY] = {
		"version": 1, "minor_version": 1, "key": STORAGE_KEY,
		"data": {"sam_current_energy": {"value": 4800.0}},
	}
	mock_restore_cache(hass, [State("sensor.sam_current_energy", "4743.95")])
	_power(hass, 720)
	entry = await _setup(hass)
	assert _sensor(hass, entry).total == pytest.approx(4800.0)


async def test_rounding_of_the_recorded_state_is_not_a_lagging_store(
		enable_custom_integrations, hass: HomeAssistant, hass_storage, caplog) -> None:
	"""0.1.3 warned "stored 1405.8053 is behind the recorded 1405.8053" on every restart."""
	hass_storage[STORAGE_KEY] = {
		"version": 1, "minor_version": 1, "key": STORAGE_KEY,
		"data": {"sam_current_energy": {"value": 1405.80527}},
	}
	mock_restore_cache(hass, [State("sensor.sam_current_energy", "1405.8053")])
	_power(hass, 720)
	entry = await _setup(hass)
	assert _sensor(hass, entry).total == pytest.approx(1405.80527, abs=1e-9)
	assert "is behind the last recorded" not in caplog.text
