from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

BASE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_sensor_generator"


def _load_module(name: str, relative_path: str):
	module_path = BASE_DIR / relative_path
	spec = spec_from_file_location(name, module_path)
	module = module_from_spec(spec)
	assert spec and spec.loader
	sys.modules[name] = module
	spec.loader.exec_module(module)
	return module


_hourly = _load_module("energy_sensor_generator_hourly_copy_service", "hourly_copy_service.py")
_resolve_copy_request = _hourly._resolve_copy_request
_pick_best_state = _hourly._pick_best_state


def _make_state(value: str, ts: datetime):
	return SimpleNamespace(state=value, last_updated=ts)


def test_resolve_copy_request_with_explicit_source():
	request = _resolve_copy_request({"target_datetime": "2025-09-30T15:00:00+00:00"})
	assert request.source_utc == datetime(2025, 9, 30, 15, 0, tzinfo=timezone.utc)
	assert request.hour_to_fix_utc is None
	assert request.hours_back == 1


def test_resolve_copy_request_using_hour_to_fix():
	request = _resolve_copy_request({
		"hour_to_fix": "2025-09-30T16:00:00+00:00",
		"hours_back": 2,
	})
	assert request.source_utc == datetime(2025, 9, 30, 14, 0, tzinfo=timezone.utc)
	assert request.hour_to_fix_utc == datetime(2025, 9, 30, 16, 0, tzinfo=timezone.utc)
	assert request.hours_back == 2


def test_resolve_copy_request_requires_fields():
	with pytest.raises(ValueError):
		_resolve_copy_request({})


def test_pick_best_state_prefers_previous_values():
	target = datetime(2025, 9, 30, 15, 0, tzinfo=timezone.utc)
	states = [
		_make_state("5.1", target - timedelta(minutes=10)),
		_make_state("5.4", target + timedelta(minutes=5)),
	]
	value, ts = _pick_best_state(states, target)
	assert value == 5.1
	assert ts == states[0].last_updated


def test_pick_best_state_falls_back_to_future_when_needed():
	target = datetime(2025, 9, 30, 15, 0, tzinfo=timezone.utc)
	states = [
		_make_state("bad", target - timedelta(minutes=5)),
		_make_state("6.2", target + timedelta(minutes=3)),
	]
	value, ts = _pick_best_state(states, target)
	assert value == 6.2
	assert ts == states[1].last_updated

