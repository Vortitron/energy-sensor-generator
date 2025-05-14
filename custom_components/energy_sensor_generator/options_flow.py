from homeassistant import config_entries
import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from .const import DOMAIN

class EnergySensorGeneratorOptionsFlow(config_entries.OptionsFlow):
	def __init__(self, config_entry):
		self.config_entry = config_entry

	async def async_step_init(self, user_input=None):
		"""Manage the options for the integration."""
		hass = self.config_entry.hass
		entity_registry = er.async_get(hass)
		# Find all power sensors
		all_power_sensors = [
			entity.entity_id
			for entity in entity_registry.entities.values()
			if entity.entity_id.startswith("sensor.")
			and entity.unit_of_measurement == "W"
			and entity.device_class == "power"
		]

		# Use current selection if present
		current = self.config_entry.options.get("selected_power_sensors", [])

		if not all_power_sensors:
			return self.async_abort(reason="no_power_sensors")

		data_schema = vol.Schema({
			vol.Optional(
				"selected_power_sensors",
				default=current or all_power_sensors
			): vol.All(vol.EnsureList(), vol.In(all_power_sensors))
		})

		if user_input is not None:
			return self.async_create_entry(title="Power Sensors", data=user_input)

		return self.async_show_form(
			step_id="init",
			data_schema=data_schema,
			description_placeholders={
				"count": str(len(all_power_sensors))
			}
		) 