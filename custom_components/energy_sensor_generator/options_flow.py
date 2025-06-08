from homeassistant import config_entries
import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
	EntitySelector, 
	EntitySelectorConfig,
	SelectSelector,
	SelectSelectorConfig,
	SelectSelectorMode,
	BooleanSelector,
	NumberSelector,
	NumberSelectorConfig,
	NumberSelectorMode
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
		
		# Get current settings
		create_daily = self.config_entry.options.get("create_daily_sensors", True)
		create_monthly = self.config_entry.options.get("create_monthly_sensors", True)
		sample_interval = self.config_entry.options.get("sample_interval", 60)
		
		# Merge auto-detected and previously selected sensors for the selection list
		all_power_sensors = {}
		
		# Get entity registry to retrieve friendly names
		entity_registry = er.async_get(hass)
		# Get device registry
		device_registry = dr.async_get(hass)
		
		# Check which current sensors still exist
		validated_current_sensors = []
		for sensor_id in current_sensors:
			state = hass.states.get(sensor_id)
			if state is not None:
				validated_current_sensors.append(sensor_id)
		
		# Add auto-detected sensors
		for sensor in auto_detected_sensors:
			# Get friendly name from entity registry
			entity_id = sensor
			entity = entity_registry.async_get(entity_id)
			friendly_name = entity.name if entity and entity.name else entity_id
			
			# Get device name if available
			device_name = None
			if entity and entity.device_id:
				device = device_registry.async_get(entity.device_id)
				if device and device.name:
					device_name = device.name
			
			# Use device name if available, otherwise use friendly name
			display_name = device_name if device_name else friendly_name
			all_power_sensors[sensor] = display_name
			
		# Add custom sensors that were previously selected
		for sensor in validated_current_sensors:
			if sensor not in all_power_sensors:
				entity = entity_registry.async_get(sensor)
				friendly_name = entity.name if entity and entity.name else sensor
				
				# Get device name if available
				device_name = None
				if entity and entity.device_id:
					device = device_registry.async_get(entity.device_id)
					if device and device.name:
						device_name = device.name
				
				# Use device name if available, otherwise use friendly name
				display_name = device_name if device_name else friendly_name
				all_power_sensors[sensor] = display_name
		
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
			
			# Get other options
			create_daily = user_input.get("create_daily_sensors", True)
			create_monthly = user_input.get("create_monthly_sensors", True)
			sample_interval = user_input.get("sample_interval", 60)
			
			if not self._errors:
				return self.async_create_entry(
					title="Power Sensors", 
					data={
						"selected_power_sensors": selected_sensors,
						"create_daily_sensors": create_daily,
						"create_monthly_sensors": create_monthly,
						"sample_interval": sample_interval
					}
				)

		# Create individual checkbox for each sensor
		schema = {}
		
		# Add checkboxes for each sensor
		for sensor_id, display_name in all_power_sensors.items():
			# Only show as selected if it was previously selected
			is_selected = sensor_id in validated_current_sensors
			schema[vol.Optional(f"sensor_{sensor_id}", default=is_selected, description=display_name)] = bool
		
		# Add custom sensor field
		schema[vol.Optional("custom_power_sensor")] = EntitySelector(
			EntitySelectorConfig(domain="sensor", multiple=False)
		)
		
		# Add sensor type options
		schema[vol.Optional("create_daily_sensors", default=create_daily)] = BooleanSelector()
		schema[vol.Optional("create_monthly_sensors", default=create_monthly)] = BooleanSelector()
		
		# Add sampling interval
		schema[vol.Optional("sample_interval", default=sample_interval)] = NumberSelector(
			NumberSelectorConfig(
				min=5,
				max=300,
				step=5,
				unit_of_measurement="seconds",
				mode=NumberSelectorMode.SLIDER
			)
		)
		
		return self.async_show_form(
			step_id="init",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"count": len(all_power_sensors),
				"daily_description": "Create daily energy sensors that reset at midnight",
				"monthly_description": "Create monthly energy sensors that reset at the beginning of each month",
				"interval_description": "Sampling interval for energy calculations (shorter intervals are more accurate but use more resources)",
				"restart_note": "⚠️ Note: Some changes may require a Home Assistant restart to take full effect. If sensors are removed, you may need to restart and then delete any entities marked as 'no longer provided'."
			}
		) 