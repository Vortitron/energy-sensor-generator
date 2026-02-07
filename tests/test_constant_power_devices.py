import pytest


def test_derive_constant_base_name_instances():
	from custom_components.energy_sensor_generator.utils import derive_constant_base_name

	device = {"switch_entity_id": "switch.boiler"}
	assert derive_constant_base_name(device) == "boiler_constant"
	assert derive_constant_base_name(device, 1) == "boiler_constant_1"
	assert derive_constant_base_name(device, 3) == "boiler_constant_3"


def test_expand_constant_power_devices_splits_evenly():
	from custom_components.energy_sensor_generator.utils import expand_constant_power_devices
	from custom_components.energy_sensor_generator.const import (
		CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID,
		CONF_CONSTANT_DEVICE_POWER_W,
		CONF_CONSTANT_DEVICE_NAME,
		CONF_CONSTANT_DEVICE_INSTANCES,
	)

	devices = [
		{
			CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID: "switch.boiler",
			CONF_CONSTANT_DEVICE_POWER_W: 9000,
			CONF_CONSTANT_DEVICE_INSTANCES: 3,
			CONF_CONSTANT_DEVICE_NAME: "Boiler",
		}
	]

	expanded = expand_constant_power_devices(devices)
	assert len(expanded) == 3
	base_names = [base for base, _ in expanded]
	assert base_names == ["boiler_constant_1", "boiler_constant_2", "boiler_constant_3"]

	for idx, (_, cfg) in enumerate(expanded, start=1):
		assert cfg[CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID] == "switch.boiler"
		assert cfg[CONF_CONSTANT_DEVICE_INSTANCES] == 3
		assert cfg["instance"] == idx
		assert cfg["total_power_w"] == 9000
		assert cfg[CONF_CONSTANT_DEVICE_POWER_W] == pytest.approx(3000.0)
		assert cfg[CONF_CONSTANT_DEVICE_NAME] == f"Boiler {idx}"


def test_format_constant_power_devices_text_includes_split_info():
	from custom_components.energy_sensor_generator.utils import format_constant_power_devices_text
	from custom_components.energy_sensor_generator.const import (
		CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID,
		CONF_CONSTANT_DEVICE_POWER_W,
		CONF_CONSTANT_DEVICE_NAME,
		CONF_CONSTANT_DEVICE_INSTANCES,
	)

	devices = [
		{
			CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID: "switch.boiler",
			CONF_CONSTANT_DEVICE_POWER_W: 9000,
			CONF_CONSTANT_DEVICE_INSTANCES: 3,
			CONF_CONSTANT_DEVICE_NAME: "Boiler",
		}
	]

	text = format_constant_power_devices_text(devices)
	assert "switch.boiler" in text
	assert "9000" in text
	assert "x3" in text
	assert "3000" in text
	assert "each" in text
	assert "Boiler" in text




