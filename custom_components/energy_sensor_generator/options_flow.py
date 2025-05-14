from homeassistant import config_entries
import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN

class EnergySensorGeneratorOptionsFlow(config_entries.OptionsFlow):
	def __init__(self, config_entry):
		"""Initialize options flow."""
		# Store config_entry as an instance attribute without calling super with it
		super().__init__()
		self._config_entry = config_entry

	async def async_step_init(self, user_input=None):
		"""Manage the options for the integration."""
		hass = self.hass
		entity_registry = er.async_get(hass)
		# Find all power sensors
		all_power_sensors = [
			entity.entity_id
			for entity in entity_registry.entities.values()
			if entity.entity_id.startswith("sensor.")
			and entity.unit_of_measurement == "W"
			and entity.device_class == "power"
		]

		# Use current selections if present
		current_sensors = self._config_entry.options.get("selected_power_sensors", [])
		current_custom = self._config_entry.options.get("custom_power_sensors", "")

		errors = {}
		
		if user_input is not None:
			selected_sensors = user_input.get("selected_power_sensors", [])
			custom_sensors_str = user_input.get("custom_power_sensors", "")
			
			# Process custom sensors
			custom_sensors = []
			if custom_sensors_str:
				custom_sensors = [s.strip() for s in custom_sensors_str.split(",") if s.strip()]
				
				# Validate that all custom entries are valid entity ID format
				invalid_entities = [s for s in custom_sensors if not s.startswith("sensor.")]
				if invalid_entities:
					errors["custom_power_sensors"] = "invalid_entity_format"
			
			if not errors:
				# Combine selected and custom sensors, removing duplicates
				all_sensors = list(set(selected_sensors + custom_sensors))
				
				return self.async_create_entry(title="Power Sensors", data={
					"selected_power_sensors": all_sensors,
					"custom_power_sensors": custom_sensors_str
				})

		if not all_power_sensors and not current_sensors and not current_custom:
			return self.async_show_form(
				step_id="init",
				data_schema=vol.Schema({
					vol.Required("custom_power_sensors", default=""): str
				}),
				description_placeholders={"count": "0"},
				errors=errors
			)

		# Set up schema with both multi-select and text input
		data_schema = vol.Schema({
			vol.Optional(
				"selected_power_sensors",
				default=current_sensors or all_power_sensors
			): cv.multi_select(all_power_sensors),
			vol.Optional(
				"custom_power_sensors", 
				description="Enter comma-separated entity IDs for additional power sensors (e.g., sensor.my_power,sensor.another_power)",
				default=current_custom
			): str
		})

		return self.async_show_form(
			step_id="init",
			data_schema=data_schema,
			description_placeholders={
				"count": str(len(all_power_sensors))
			},
			errors=errors
		) 