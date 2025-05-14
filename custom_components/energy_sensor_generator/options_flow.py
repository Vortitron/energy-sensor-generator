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
		# Store config_entry as an instance attribute without calling super with it
		super().__init__()
		self._config_entry = config_entry
		self._errors = {}

	async def async_step_init(self, user_input=None):
		"""Manage the options for the integration."""
		self._errors = {}
		
		hass = self.hass
		
		# Get auto-detected power sensors
		all_power_sensors = detect_power_sensors(hass)
		
		# Use current selections if present
		current_sensors = self._config_entry.options.get("selected_power_sensors", [])
		current_custom = self._config_entry.options.get("custom_power_sensors", "")
		
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
					data={
						"selected_power_sensors": all_sensors,
						"custom_power_sensors": current_custom + ("," + custom_sensor if custom_sensor and current_custom else custom_sensor)
					}
				)

		# Show different forms based on whether any sensors were auto-detected
		if not all_power_sensors and not current_sensors:
			# No auto-detected sensors, just show custom sensor field
			return self.async_show_form(
				step_id="init",
				data_schema=vol.Schema({
					vol.Optional("custom_power_sensor"): EntitySelector(
						EntitySelectorConfig(
							domain="sensor",
							multiple=False
						)
					)
				}),
				errors=self._errors
			)
		
		# Show both auto-detected and custom input
		data_schema = vol.Schema({
			vol.Optional(
				"selected_power_sensors",
				default=current_sensors or all_power_sensors
			): cv.multi_select(all_power_sensors),
			vol.Optional(
				"custom_power_sensor",
				description="Additional power sensor entity (with autocomplete)"
			): EntitySelector(
				EntitySelectorConfig(
					domain="sensor",
					multiple=False
				)
			)
		})
		
		return self.async_show_form(
			step_id="init",
			data_schema=data_schema,
			description_placeholders={
				"count": str(len(all_power_sensors))
			},
			errors=self._errors
		) 