from homeassistant import config_entries
import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig
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
			selected_sensors = user_input.get("selected_power_sensors", [])
			custom_sensor = user_input.get("custom_power_sensor", "")
			
			# Process and save
			all_sensors = list(selected_sensors)
			
			# Add custom sensor if provided
			if custom_sensor and custom_sensor not in all_sensors:
				all_sensors.append(custom_sensor)
			
			if not self._errors:
				return self.async_create_entry(
					title="Power Sensors", 
					data={"selected_power_sensors": all_sensors}
				)

		schema = vol.Schema({
			vol.Optional(
				"selected_power_sensors",
				default=current_sensors
			): cv.multi_select(all_power_sensors),
			vol.Optional(
				"custom_power_sensor",
				description={"text": "Additional power sensor entity to add to the list"}
			): EntitySelector(
				EntitySelectorConfig(
					domain="sensor",
					multiple=False
				)
			)
		})
		
		return self.async_show_form(
			step_id="init",
			data_schema=schema,
			errors=self._errors
		) 