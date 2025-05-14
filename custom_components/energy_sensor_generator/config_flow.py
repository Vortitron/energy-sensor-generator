"""Config flow for Energy Sensor Generator integration."""
from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN
from .options_flow import EnergySensorGeneratorOptionsFlow

class EnergySensorGeneratorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Energy Sensor Generator."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title="Energy Sensor Generator", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional("auto_generate", default=True): bool,
                vol.Optional("update_interval", default=60): int
            })
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return EnergySensorGeneratorOptionsFlow(config_entry)
