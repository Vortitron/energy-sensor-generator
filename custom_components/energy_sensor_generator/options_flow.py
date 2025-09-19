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
	CONF_CREATE_SYNTHETIC_GRID_TOTAL,
	CONF_FORCE_STATISTICAL_ONLY,
	CONF_STAT_LOOKBACK_MINUTES
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
		force_statistical_only = defaults.get(CONF_FORCE_STATISTICAL_ONLY, False)
		stat_lookback = defaults.get(CONF_STAT_LOOKBACK_MINUTES, 60)
		
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
			
			# Create comprehensive display name
			if device_name and friendly_name and device_name != friendly_name:
				# Check if friendly name already contains device name to avoid redundancy
				if device_name.lower() in friendly_name.lower():
					display_name = friendly_name
				else:
					# Show both device and sensor name if they're different
					display_name = f"{device_name} - {friendly_name}"
			elif device_name:
				# Use device name if sensor name is generic or missing
				display_name = device_name
			else:
				# Fallback to friendly name or entity ID
				display_name = friendly_name
			
			# Add entity ID suffix for disambiguation if needed (useful for debugging)
			# Only show if display name doesn't clearly identify the sensor
			if not any(part in display_name.lower() for part in ['power', 'energy', 'watt']) and entity_id != display_name:
				display_name = f"{display_name} ({entity_id.split('.')[-1]})"
			
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
				
				# Create comprehensive display name
				if device_name and friendly_name and device_name != friendly_name:
					# Check if friendly name already contains device name to avoid redundancy
					if device_name.lower() in friendly_name.lower():
						display_name = friendly_name
					else:
						# Show both device and sensor name if they're different
						display_name = f"{device_name} - {friendly_name}"
				elif device_name:
					# Use device name if sensor name is generic or missing
					display_name = device_name
				else:
					# Fallback to friendly name or entity ID
					display_name = friendly_name
				
				# Add entity ID suffix for disambiguation if needed (useful for debugging)
				# Only show if display name doesn't clearly identify the sensor
				if not any(part in display_name.lower() for part in ['power', 'energy', 'watt']) and sensor != display_name:
					display_name = f"{display_name} ({sensor.split('.')[-1]})"
				
				all_power_sensors[sensor] = display_name
		
		if user_input is not None:
			# Get selected sensors from multi-select
			selected_sensors = user_input.get("selected_power_sensors", [])
			
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
			force_statistical_only = user_input.get(CONF_FORCE_STATISTICAL_ONLY, False)
			stat_lookback = user_input.get(CONF_STAT_LOOKBACK_MINUTES, 60)
			
			if not self._errors:
				# Create the configuration entry first
				result = self.async_create_entry(
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
						CONF_CREATE_SYNTHETIC_GRID_TOTAL: synthetic_grid_total,
						CONF_FORCE_STATISTICAL_ONLY: force_statistical_only,
						CONF_STAT_LOOKBACK_MINUTES: stat_lookback
					}
				)

				# Automatically generate sensors for the new configuration
				hass = self.hass
				hass.async_create_task(self._async_generate_sensors_after_config())
				
				return result

		# Create schema for sensor selection
		schema = {}
		
		# Use MultiSelectSelector for better display of sensor names
		sensor_options = []
		for sensor_id, display_name in all_power_sensors.items():
			sensor_options.append({
				"value": sensor_id,
				"label": display_name
			})
		
		schema[vol.Optional("selected_power_sensors", default=validated_current_sensors)] = SelectSelector(
			SelectSelectorConfig(
				options=sensor_options,
				multiple=True,
				mode=SelectSelectorMode.DROPDOWN
			)
		)
		
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
		force_statistical_only = defaults.get(CONF_FORCE_STATISTICAL_ONLY, False)
		stat_lookback = defaults.get(CONF_STAT_LOOKBACK_MINUTES, 60)
		
		if user_input is not None:
			# Update defaults with advanced settings
			self._user_defaults.update(user_input)
			
			# Process all the data and save
			all_data = {**self._user_defaults}
			
			# Get selected sensors from user defaults
			selected_sensors = all_data.get("selected_power_sensors", [])
			
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
			force_statistical_only = user_input.get(CONF_FORCE_STATISTICAL_ONLY, False)
			stat_lookback = user_input.get(CONF_STAT_LOOKBACK_MINUTES, 60)
			
			# Create the configuration entry
			result = self.async_create_entry(
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
					CONF_CREATE_SYNTHETIC_GRID_TOTAL: synthetic_grid_total,
					CONF_FORCE_STATISTICAL_ONLY: force_statistical_only,
					CONF_STAT_LOOKBACK_MINUTES: stat_lookback
				}
			)
			
			# Automatically generate sensors for the new configuration
			hass = self.hass
			hass.async_create_task(self._async_generate_sensors_after_config())
			
			return result

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
		schema[vol.Optional(CONF_FORCE_STATISTICAL_ONLY, default=force_statistical_only)] = BooleanSelector()
		schema[vol.Optional(CONF_STAT_LOOKBACK_MINUTES, default=stat_lookback)] = NumberSelector(
			NumberSelectorConfig(
				min=5,
				max=120,
				step=5,
				unit_of_measurement="minutes",
				mode=NumberSelectorMode.SLIDER
			)
		)
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
	
	async def _async_generate_sensors_after_config(self):
		"""Generate sensors automatically after configuration is saved."""
		import asyncio
		import logging
		_LOGGER = logging.getLogger(__name__)
		
		# Wait a short time for the config entry to be fully processed
		await asyncio.sleep(2)
		
		try:
			# Import the generate_sensors_service function
			from .__init__ import generate_sensors_service
			
			_LOGGER.info("Auto-generating energy sensors after configuration update...")
			
			# Call the service to generate sensors
			await generate_sensors_service(self.hass, None, self.config_entry)
			
			_LOGGER.info("Energy sensors generated successfully after configuration update")
			
			# Send a persistent notification to the user (use services API; components attribute is no longer available)
			await self.hass.services.async_call(
				"persistent_notification",
				"create",
				{
					"message": "Energy sensors have been created automatically based on your new configuration. Check the Entities page to see your new sensors.",
					"title": "Energy Sensor Generator",
					"notification_id": "energy_sensor_generator_created"
				},
				blocking=False
			)
			
		except Exception as e:
			_LOGGER.error(f"Failed to auto-generate sensors after configuration: {e}")
			
			# Send error notification to user
			await self.hass.services.async_call(
				"persistent_notification",
				"create",
				{
					"message": f"Failed to automatically create energy sensors: {str(e)}. You may need to manually reload the integration.",
					"title": "Energy Sensor Generator - Error",
					"notification_id": "energy_sensor_generator_error"
				},
				blocking=False
			)