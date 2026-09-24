"""Provides device actions for energy_sensor_generator."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN

ACTION_TYPES = {
	"generate_sensors",
	"reset_energy_sensors", 
	"debug_sensor_detection",
	"diagnose_sensor",
	"list_sensors",
	"export_energy_data"
}

ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
	{
		vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
		vol.Optional("reset_factor"): vol.Coerce(float),
		vol.Optional("reset_to_zero"): bool,
		vol.Optional("sensor_name"): str,
		vol.Optional("target_path"): str,
	}
)


async def async_get_actions(
	hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
	"""List device actions for energy_sensor_generator devices."""
	device_registry = dr.async_get(hass)
	device = device_registry.async_get(device_id)
	
	actions = []
	
	if device and any(identifier[0] == DOMAIN for identifier in device.identifiers):
		actions.extend([
			{
				CONF_DEVICE_ID: device_id,
				CONF_DOMAIN: DOMAIN,
				CONF_TYPE: "generate_sensors",
				"name": "Generate Energy Sensors"
			},
			{
				CONF_DEVICE_ID: device_id,
				CONF_DOMAIN: DOMAIN,
				CONF_TYPE: "reset_energy_sensors",
				"name": "Reset Energy Sensors"
			},
			{
				CONF_DEVICE_ID: device_id,
				CONF_DOMAIN: DOMAIN,
				CONF_TYPE: "debug_sensor_detection",
				"name": "Debug Sensor Detection"
			},
			{
				CONF_DEVICE_ID: device_id,
				CONF_DOMAIN: DOMAIN,
				CONF_TYPE: "diagnose_sensor",
				"name": "Diagnose Sensor"
			},
			{
				CONF_DEVICE_ID: device_id,
				CONF_DOMAIN: DOMAIN,
				CONF_TYPE: "list_sensors", 
				"name": "List Energy Sensors"
			},
			{
				CONF_DEVICE_ID: device_id,
				CONF_DOMAIN: DOMAIN,
				CONF_TYPE: "export_energy_data",
				"name": "Export Energy Data"
			}
		])
	
	return actions


# Extra fields each action forwards to its service (see async_get_action_capabilities)
ACTION_FIELDS = {
	"reset_energy_sensors": ("reset_factor", "reset_to_zero"),
	"diagnose_sensor": ("sensor_name",),
	"export_energy_data": ("target_path",),
}


async def async_call_action_from_config(
	hass: HomeAssistant,
	config: dict,
	variables: dict,
	context: Context | None
) -> None:
	"""Execute a device action."""
	action_type = config[CONF_TYPE]
	service_data = {
		key: config[key]
		for key in ACTION_FIELDS.get(action_type, ())
		if key in config
	}
	await hass.services.async_call(
		DOMAIN,
		action_type,
		service_data,
		blocking=True,
		context=context,
	)


async def async_get_action_capabilities(
	hass: HomeAssistant, config: dict
) -> dict[str, vol.Schema]:
	"""List action capabilities."""
	action_type = config[CONF_TYPE]
	
	if action_type == "reset_energy_sensors":
		return {
			"extra_fields": vol.Schema({
				vol.Optional("reset_factor", default=0.5): vol.Coerce(float),
				vol.Optional("reset_to_zero", default=False): bool,
			})
		}
	
	if action_type == "diagnose_sensor":
		return {
			"extra_fields": vol.Schema({
				vol.Required("sensor_name"): str,
			})
		}
	
	if action_type == "export_energy_data":
		return {
			"extra_fields": vol.Schema({
				vol.Optional("target_path"): str,
			})
		}
	
	return {}
