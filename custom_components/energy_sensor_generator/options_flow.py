from homeassistant import config_entries
import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
	EntitySelector, 
	EntitySelectorConfig,
	SelectSelector,
	SelectSelectorConfig,
	SelectSelectorMode,
	BooleanSelector
)
from .const import DOMAIN
from .__init__ import detect_power_sensors  # Import the detect function

class EnergySensorGeneratorOptionsFlow(config_entries.OptionsFlow):
	def __init__(self, config_entry):
		"""Initialize options flow."""
		self.config_entry = config_entry
		self.options = dict(config_entry.options)
		self._errors = {}

	async def async_step_init(self, user_input=None):
		"""Manage the options for the integration."""
		self._errors = {}
		
		hass = self.hass
		
		# Get auto-detected power sensors
		auto_detected_sensors = detect_power_sensors(hass)
		
		# Get current selections if present
		current_sensors = self.config_entry.options.get("selected_power_sensors", [])
		
		# Merge auto-detected and previously selected sensors for the selection list
		all_power_sensors = {}
		
		# Add auto-detected sensors
		for sensor in auto_detected_sensors:
			all_power_sensors[sensor] = sensor
			
		# Add custom sensors that were previously selected
		for sensor in current_sensors:
			if sensor not in all_power_sensors:
				all_power_sensors[sensor] = sensor
		
		if user_input is not None:
			selected_sensors = []
			
			# Process checkbox selections
			for sensor_id, selected in user_input.items():
				if sensor_id.startswith("sensor_") and selected:
					# Extract the actual sensor ID from the field name
					actual_sensor_id = sensor_id[7:]  # Remove "sensor_" prefix
					selected_sensors.append(actual_sensor_id)
			
			# Add custom sensor if provided
			custom_sensor = user_input.get("custom_power_sensor", "")
			if custom_sensor and custom_sensor not in selected_sensors:
				selected_sensors.append(custom_sensor)
			
			if not self._errors:
				return self.async_create_entry(
					title="Power Sensors", 
					data={"selected_power_sensors": selected_sensors}
				)

		# Create individual checkbox for each sensor
		schema = {}
		
		# Add checkboxes for each sensor
		for sensor_id in all_power_sensors:
			is_selected = sensor_id in current_sensors
			schema[vol.Optional(f"sensor_{sensor_id}", default=is_selected)] = bool
		
		# Add custom sensor field
		schema[vol.Optional("custom_power_sensor")] = EntitySelector(
			EntitySelectorConfig(domain="sensor", multiple=False)
		)
		
		return self.async_show_form(
			step_id="init",
			data_schema=vol.Schema(schema),
			errors=self._errors
		) 