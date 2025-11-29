from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

BASE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "energy_sensor_generator"


def _load_module(name: str, relative_path: str):
	module_path = BASE_DIR / relative_path
	spec = spec_from_file_location(name, module_path)
	module = module_from_spec(spec)
	assert spec and spec.loader
	spec.loader.exec_module(module)
	return module


_const = _load_module("energy_sensor_generator_const", "const.py")
_helpers = _load_module("energy_sensor_generator_device_helpers", "device_helpers.py")

DOMAIN = _const.DOMAIN
has_external_energy_sensors = _helpers.has_external_energy_sensors


class DummyRegistry:
	def __init__(self, entries):
		self._entries = dict(entries)

	def async_get(self, entity_id):
		return self._entries.get(entity_id)


def test_detects_external_energy_sensors():
	device_map = {"dev-1": ["sensor.one_energy", "sensor.other_energy"]}
	registry = DummyRegistry({
		"sensor.one_energy": SimpleNamespace(platform=DOMAIN),
		"sensor.other_energy": SimpleNamespace(platform="other_integration"),
	})

	has_external, sensors = has_external_energy_sensors("dev-1", device_map, registry.async_get)

	assert has_external is True
	assert sensors == device_map["dev-1"]


def test_only_internal_energy_sensors_are_not_treated_as_conflict():
	device_map = {"dev-2": ["sensor.local_energy"]}
	registry = DummyRegistry({
		"sensor.local_energy": SimpleNamespace(platform=DOMAIN),
	})

	has_external, sensors = has_external_energy_sensors("dev-2", device_map, registry.async_get)

	assert has_external is False
	assert sensors == device_map["dev-2"]


def test_missing_device_returns_false_and_empty_list():
	device_map = {"dev-3": ["sensor.exists"]}
	registry = DummyRegistry({
		"sensor.exists": SimpleNamespace(platform="other"),
	})

	has_external, sensors = has_external_energy_sensors("unknown", device_map, registry.async_get)

	assert has_external is False
	assert sensors == []

