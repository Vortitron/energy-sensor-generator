"""Config flow for Energy Sensor Generator integration."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.selector import (
	NumberSelector,
	NumberSelectorConfig,
	NumberSelectorMode,
	BooleanSelector,
)
from .const import DOMAIN, CONF_DEBUG_LOGGING


class EnergySensorGeneratorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
	"""Handle a config flow for Energy Sensor Generator."""

	VERSION = 1

	async def async_step_user(self, user_input=None):
		"""Handle the initial step."""
		errors = {}

		if user_input is not None:
			await self.async_set_unique_id(DOMAIN)
			self._abort_if_unique_id_configured()

			return self.async_create_entry(
				title="Energy Sensor Generator",
				data={
					"sample_interval": user_input.get("sample_interval", 60),
				},
				options={
					CONF_DEBUG_LOGGING: user_input.get(CONF_DEBUG_LOGGING, False),
				},
			)

		return self.async_show_form(
			step_id="user",
			data_schema=vol.Schema({
				vol.Optional("sample_interval", default=60): NumberSelector(
					NumberSelectorConfig(
						min=5,
						max=300,
						step=5,
						unit_of_measurement="seconds",
						mode=NumberSelectorMode.SLIDER,
					)
				),
				vol.Optional(CONF_DEBUG_LOGGING, default=False): BooleanSelector(),
			}),
			errors=errors,
		)

	@staticmethod
	def async_get_options_flow(config_entry):
		"""Get the options flow for this handler."""
		from .options_flow import EnergySensorGeneratorOptionsFlow
		return EnergySensorGeneratorOptionsFlow()
