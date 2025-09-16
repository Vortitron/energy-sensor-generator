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
from .const import (
	DOMAIN, 
	CONF_DEBUG_LOGGING, 
	CONF_USE_STATISTICAL, 
	CONF_CREATE_SYNTHETIC_GRID_TOTAL
)
from .__init__ import detect_power_sensors  # Import the detect function

class EnergySensorGeneratorOptionsFlow(config_entries.OptionsFlow):
	def __init__(self, config_entry):
		"""Initialize options flow."""
		self.options = dict(config_entry.options)
		self._errors = {}
		self._user_defaults = {}
		self._show_advanced = False

	async def async_step_init(self, user_input=None):
		"""Manage the options for the integration."""
		self._errors = {}
		
		hass = self.hass
		
		# Get auto-detected power sensors
		auto_detected_sensors = detect_power_sensors(hass)
		
		# Get current selections if present
		current_sensors = self.config_entry.options.get("selected_power_sensors", [])
		
		# Handle navigation to advanced step
		if user_input is not None:
			requested_show_advanced = user_input.get("show_advanced", False)
			# Navigate to advanced step if requested
			if requested_show_advanced:
				self._user_defaults = dict(user_input)
				return await self.async_step_advanced()
		else:
			requested_show_advanced = False
		
		# Compose defaults from saved options and any prior user edits
		defaults = {**self.config_entry.options, **self._user_defaults}
		
		# Get current settings
		create_daily = defaults.get("create_daily_sensors", True)
		create_monthly = defaults.get("create_monthly_sensors", True)
		create_weekly = defaults.get("create_weekly_sensors", True)
		create_annual = defaults.get("create_annual_sensors", True)
		
		# Advanced settings (hidden by default)
		sample_interval = defaults.get("sample_interval", 60)
		debug_logging = defaults.get(CONF_DEBUG_LOGGING, False)
		use_statistical = defaults.get(CONF_USE_STATISTICAL, True)
		synthetic_grid_total = defaults.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False)

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
			
			# Get period sensor options - parse from multi-select
			period_sensors = user_input.get("period_sensors", ["daily", "monthly", "weekly", "annual"])
			create_daily = "daily" in period_sensors
			create_monthly = "monthly" in period_sensors
			create_weekly = "weekly" in period_sensors
			create_annual = "annual" in period_sensors
			
			# Advanced options (only if advanced is shown)
			sample_interval = user_input.get("sample_interval", 60)
			debug_logging = user_input.get(CONF_DEBUG_LOGGING, False)
			use_statistical = user_input.get(CONF_USE_STATISTICAL, True)
			synthetic_grid_total = user_input.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False)

			if not self._errors:
				return self.async_create_entry(
					title="Power Sensors", 
					data={
						"selected_power_sensors": selected_sensors,
						"create_daily_sensors": create_daily,
						"create_monthly_sensors": create_monthly,
						"create_weekly_sensors": create_weekly,
						"create_annual_sensors": create_annual,
						"sample_interval": sample_interval,
						CONF_DEBUG_LOGGING: debug_logging,
						CONF_USE_STATISTICAL: use_statistical,
						CONF_CREATE_SYNTHETIC_GRID_TOTAL: synthetic_grid_total
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
		
		# Create period sensors multi-select
		default_periods = []
		if create_daily:
			default_periods.append("daily")
		if create_monthly:
			default_periods.append("monthly")
		if create_weekly:
			default_periods.append("weekly")
		if create_annual:
			default_periods.append("annual")
		
		schema[vol.Optional("period_sensors", default=default_periods)] = SelectSelector(
			SelectSelectorConfig(
				options=[
					{"value": "daily", "label": "Daily"},
					{"value": "weekly", "label": "Weekly"},
					{"value": "monthly", "label": "Monthly"},
					{"value": "annual", "label": "Annual"}
				],
				multiple=True,
				mode=SelectSelectorMode.DROPDOWN
			)
		)
		
		
		# Show advanced (navigates to next step)
		schema[vol.Optional("show_advanced", default=False)] = BooleanSelector()
		
		# Note: Advanced fields moved to dedicated step
		
		return self.async_show_form(
			step_id="init",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"count": len(all_power_sensors)
			}
		) 

	async def async_step_advanced(self, user_input=None):
		"""Advanced settings step."""
		self._errors = {}
		defaults = {**self.config_entry.options, **self._user_defaults}
		sample_interval = defaults.get("sample_interval", 60)
		debug_logging = defaults.get(CONF_DEBUG_LOGGING, False)
		use_statistical = defaults.get(CONF_USE_STATISTICAL, True)
		synthetic_grid_total = defaults.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False)
		
		if user_input is not None:
			# Update defaults with advanced settings
			self._user_defaults.update(user_input)
			
			# Process all the data and save
			all_data = {**self._user_defaults}
			
			# Parse sensor selections from user defaults
			selected_sensors = []
			for key, value in all_data.items():
				if key.startswith("sensor_") and value:
					actual_sensor_id = key[7:]  # Remove "sensor_" prefix
					selected_sensors.append(actual_sensor_id)
			
			# Add custom sensor if provided
			custom_sensor = all_data.get("custom_power_sensor", "")
			if custom_sensor and custom_sensor not in selected_sensors:
				selected_sensors.append(custom_sensor)
			
			# Get period sensor options
			period_sensors = all_data.get("period_sensors", ["daily", "monthly", "weekly", "annual"])
			create_daily = "daily" in period_sensors
			create_monthly = "monthly" in period_sensors
			create_weekly = "weekly" in period_sensors
			create_annual = "annual" in period_sensors
			
			# Get other options
			synthetic_grid_total = all_data.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False)
			sample_interval = user_input.get("sample_interval", 60)
			debug_logging = user_input.get(CONF_DEBUG_LOGGING, False)
			use_statistical = user_input.get(CONF_USE_STATISTICAL, True)
			
			return self.async_create_entry(
				title="Power Sensors",
				data={
					"selected_power_sensors": selected_sensors,
					"create_daily_sensors": create_daily,
					"create_monthly_sensors": create_monthly,
					"create_weekly_sensors": create_weekly,
					"create_annual_sensors": create_annual,
					"sample_interval": sample_interval,
					CONF_DEBUG_LOGGING: debug_logging,
					CONF_USE_STATISTICAL: use_statistical,
					CONF_CREATE_SYNTHETIC_GRID_TOTAL: synthetic_grid_total
				}
			)

		schema = {}
		schema[vol.Optional("sample_interval", default=sample_interval)] = NumberSelector(
			NumberSelectorConfig(
				min=5,
				max=300,
				step=5,
				unit_of_measurement="seconds",
				mode=NumberSelectorMode.SLIDER
			)
		)
		schema[vol.Optional(CONF_DEBUG_LOGGING, default=debug_logging)] = BooleanSelector()
		schema[vol.Optional(CONF_USE_STATISTICAL, default=use_statistical)] = BooleanSelector()
				# Option to create synthetic grid total
		schema[vol.Optional(CONF_CREATE_SYNTHETIC_GRID_TOTAL, default=synthetic_grid_total)] = BooleanSelector()

		return self.async_show_form(
			step_id="advanced",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"description": "Configure advanced settings for energy sensor generation."
			}
		)