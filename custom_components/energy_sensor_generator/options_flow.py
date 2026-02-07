from homeassistant import config_entries
import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
	EntitySelector, 
	EntitySelectorConfig,
	SelectSelector,
	SelectSelectorConfig,
	SelectSelectorMode,
	BooleanSelector,
	NumberSelector,
	NumberSelectorConfig,
	NumberSelectorMode,
	TextSelector,
	TextSelectorConfig
)
from .const import (
	DOMAIN, 
	CONF_DEBUG_LOGGING, 
	CONF_USE_STATISTICAL, 
	CONF_CREATE_SYNTHETIC_GRID_TOTAL,
	CONF_FORCE_STATISTICAL_ONLY,
	CONF_STAT_LOOKBACK_MINUTES,
	CONF_MAX_ENERGY_PER_HOUR,
	CONF_CONSTANT_POWER_DEVICES,
	CONF_PRICE_ADJUST_SENSORS,
	CONF_POWER_SUM_SENSORS,
	CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID,
	CONF_CONSTANT_DEVICE_POWER_W,
	CONF_CONSTANT_DEVICE_NAME,
	CONF_CONSTANT_DEVICE_INSTANCES
)
from .__init__ import detect_power_sensors  # Import the detect function
from .utils import format_constant_power_devices_text
import uuid

class EnergySensorGeneratorOptionsFlow(config_entries.OptionsFlow):
	def __init__(self, config_entry):
		"""Initialize options flow."""
		self.options = dict(config_entry.options)
		self._errors = {}
		self._user_defaults = {}
		self._constant_devices_return_step = "menu"
		self._power_sum_return_step = "menu"
		self._price_adjust_return_step = "menu"

	def _get_constant_devices(self) -> list:
		"""Return a copy of the currently edited constant device list."""
		defaults = {**self.config_entry.options, **self._user_defaults}
		return [dict(device) for device in defaults.get(CONF_CONSTANT_POWER_DEVICES, []) or []]

	def _set_constant_devices(self, devices: list) -> None:
		"""Persist the working constant device list."""
		self._user_defaults[CONF_CONSTANT_POWER_DEVICES] = [dict(device) for device in devices]
	
	def _get_price_adjustments(self) -> list:
		"""Return a copy of the currently edited price adjustment list."""
		defaults = {**self.config_entry.options, **self._user_defaults}
		return [dict(item) for item in defaults.get(CONF_PRICE_ADJUST_SENSORS, []) or []]
	
	def _set_price_adjustments(self, items: list) -> None:
		"""Persist the working price adjustment list."""
		self._user_defaults[CONF_PRICE_ADJUST_SENSORS] = [dict(item) for item in items]

	def _get_power_sums(self) -> list:
		"""Return a copy of the currently edited power sum list."""
		defaults = {**self.config_entry.options, **self._user_defaults}
		return [dict(item) for item in defaults.get(CONF_POWER_SUM_SENSORS, []) or []]

	def _set_power_sums(self, items: list) -> None:
		"""Persist the working power sum list."""
		self._user_defaults[CONF_POWER_SUM_SENSORS] = [dict(item) for item in items]

	async def async_step_init(self, user_input=None):
		"""Entry point: show the menu."""
		return await self.async_step_menu(user_input)

	async def async_step_menu(self, user_input=None):
		"""Show the options menu."""
		defaults = {**self.config_entry.options, **self._user_defaults}
		constant_devices_summary = format_constant_power_devices_text(
			defaults.get(CONF_CONSTANT_POWER_DEVICES, [])
		) or "— None configured —"
		price_adjust_summary = "— None configured —"
		try:
			if defaults.get(CONF_PRICE_ADJUST_SENSORS, []):
				price_adjust_summary = "\n".join([
					f"{item.get('name') or item.get('source_entity_id')} (+{item.get('add_amount')})"
					for item in defaults.get(CONF_PRICE_ADJUST_SENSORS, []) or []
					if item.get("source_entity_id")
				]) or "— None configured —"
		except Exception:
			price_adjust_summary = "— None configured —"
		power_sum_summary = "— None configured —"
		try:
			if defaults.get(CONF_POWER_SUM_SENSORS, []):
				power_sum_summary = "\n".join([
					f"{item.get('name') or item.get('id')} ({len(item.get('source_entity_ids') or [])} sources)"
					for item in defaults.get(CONF_POWER_SUM_SENSORS, []) or []
					if item.get("id") and (item.get("source_entity_ids") or [])
				]) or "— None configured —"
		except Exception:
			power_sum_summary = "— None configured —"

		return self.async_show_menu(
			step_id="menu",
			menu_options=[
				"sensors",
				"constant_devices",
				"power_sums",
				"price_adjustments",
				"advanced",
			],
			description_placeholders={
				"constant_summary": constant_devices_summary,
				"power_sum_summary": power_sum_summary,
				"price_adjust_summary": price_adjust_summary,
			},
		)

	async def async_step_sensors(self, user_input=None):
		"""Select power sensors and period sensors."""
		self._errors = {}
		
		hass = self.hass
		
		# Get auto-detected power sensors
		auto_detected_sensors = detect_power_sensors(hass)
		
		# Get current selections if present
		current_sensors = self.config_entry.options.get("selected_power_sensors", [])
		
		# Compose defaults from saved options and any prior user edits
		defaults = {**self.config_entry.options, **self._user_defaults}
		
		# Get current settings
		create_daily = defaults.get("create_daily_sensors", True)
		create_monthly = defaults.get("create_monthly_sensors", True)
		create_weekly = defaults.get("create_weekly_sensors", True)
		create_annual = defaults.get("create_annual_sensors", True)
		# Keep this step focused on sensor selection only; summaries live in the menu/advanced steps.
		# Advanced settings (hidden by default)
		sample_interval = defaults.get("sample_interval", 60)
		debug_logging = defaults.get(CONF_DEBUG_LOGGING, False)
		use_statistical = defaults.get(CONF_USE_STATISTICAL, True)
		synthetic_grid_total = defaults.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False)
		force_statistical_only = defaults.get(CONF_FORCE_STATISTICAL_ONLY, False)
		stat_lookback = defaults.get(CONF_STAT_LOOKBACK_MINUTES, 30)
		
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
			
			# Always add entity ID suffix for clear identification
			# This helps distinguish between similar devices (e.g., multiple smart plugs)
			entity_id_short = entity_id.replace("sensor.", "")
			display_name = f"{display_name} — {entity_id_short}"
			
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
				
				# Always add entity ID suffix for clear identification
				# This helps distinguish between similar devices (e.g., multiple smart plugs)
				sensor_id_short = sensor.replace("sensor.", "")
				display_name = f"{display_name} — {sensor_id_short}"
				
				all_power_sensors[sensor] = display_name
		
		if user_input is not None:
			# Get selected sensors from multi-select
			selected_sensors = user_input.get("selected_power_sensors", [])
			
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
						CONF_STAT_LOOKBACK_MINUTES: stat_lookback,
						CONF_CONSTANT_POWER_DEVICES: self._get_constant_devices(),
						CONF_POWER_SUM_SENSORS: self._get_power_sums(),
						CONF_PRICE_ADJUST_SENSORS: self._get_price_adjustments()
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
		
		
		return self.async_show_form(
			step_id="sensors",
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
		stat_lookback = defaults.get(CONF_STAT_LOOKBACK_MINUTES, 30)
		max_energy_per_hour = defaults.get(CONF_MAX_ENERGY_PER_HOUR, 0)  # 0 = disabled by default
		constant_devices_summary = format_constant_power_devices_text(
			defaults.get(CONF_CONSTANT_POWER_DEVICES, [])
		) or "— None configured —"
		price_adjust_summary = "— None configured —"
		try:
			if defaults.get(CONF_PRICE_ADJUST_SENSORS, []):
				price_adjust_summary = "\n".join([
					f"{item.get('name') or item.get('source_entity_id')} (+{item.get('add_amount')})"
					for item in defaults.get(CONF_PRICE_ADJUST_SENSORS, []) or []
					if item.get("source_entity_id")
				]) or "— None configured —"
		except Exception:
			price_adjust_summary = "— None configured —"
		
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
			max_energy_per_hour = user_input.get(CONF_MAX_ENERGY_PER_HOUR, 0)  # 0 = disabled by default
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
					CONF_STAT_LOOKBACK_MINUTES: stat_lookback,
					CONF_MAX_ENERGY_PER_HOUR: max_energy_per_hour,
					CONF_CONSTANT_POWER_DEVICES: self._get_constant_devices(),
					CONF_POWER_SUM_SENSORS: self._get_power_sums(),
					CONF_PRICE_ADJUST_SENSORS: self._get_price_adjustments()
				}
			)
			
			# Automatically generate sensors for the new configuration
			hass = self.hass
			hass.async_create_task(self._async_generate_sensors_after_config())
			
			return result

		schema = {}
		schema[vol.Optional("custom_power_sensor")] = EntitySelector(
			EntitySelectorConfig(domain="sensor", multiple=False)
		)
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
		schema[vol.Optional(CONF_MAX_ENERGY_PER_HOUR, default=max_energy_per_hour)] = NumberSelector(
			NumberSelectorConfig(
				min=0,
				max=100.0,
				step=0.5,
				unit_of_measurement="kWh/hour (0=disabled)",
				mode=NumberSelectorMode.BOX
			)
		)
		# Option to create synthetic grid total
		schema[vol.Optional(CONF_CREATE_SYNTHETIC_GRID_TOTAL, default=synthetic_grid_total)] = BooleanSelector()
		
		return self.async_show_form(
			step_id="advanced",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"constant_summary": constant_devices_summary,
				"power_sum_summary": power_sum_summary,
				"price_adjust_summary": price_adjust_summary
			}
		)

	async def async_step_price_adjustments(self, user_input=None):
		"""Manage electricity price add-ons (source sensor + fixed add amount)."""
		self._errors = {}
		items = self._get_price_adjustments()
		status_message = self._user_defaults.pop("_price_adjust_status", None)
		
		summary_lines = []
		for item in items:
			source = item.get("source_entity_id")
			if not source:
				continue
			name = (item.get("name") or source).strip()
			add_amount = item.get("add_amount", 0)
			summary_lines.append(f"{name} — {source} — add {add_amount}")
		price_adjust_summary = "\n".join(summary_lines) or "— None configured —"
		
		remove_options = []
		for item in items:
			item_id = item.get("id")
			source = item.get("source_entity_id")
			if not item_id or not source:
				continue
			label = (item.get("name") or source).strip()
			remove_options.append({"value": str(item_id), "label": f"{label} — {source}"})
		
		if user_input is not None:
			action = user_input.get("price_adjust_action", "finish")
			
			if action == "finish":
				next_step = self._price_adjust_return_step or "menu"
				self._price_adjust_return_step = "menu"
				if next_step == "advanced":
					return await self.async_step_advanced()
				if next_step == "sensors":
					return await self.async_step_sensors()
				return await self.async_step_menu()
			
			if action == "add":
				source_entity = user_input.get("price_adjust_source")
				add_amount = user_input.get("price_adjust_add_amount")
				friendly_name = user_input.get("price_adjust_name")
				
				if not source_entity or add_amount is None:
					self._errors["base"] = "price_adjust_missing_fields"
				else:
					try:
						add_amount_float = float(add_amount)
					except (TypeError, ValueError):
						self._errors["base"] = "price_adjust_invalid_amount"
					else:
						# Update existing entry for this source if present (keep stable id)
						existing = next((x for x in items if x.get("source_entity_id") == source_entity), None)
						if existing and existing.get("id"):
							config_id = str(existing["id"])
						else:
							config_id = uuid.uuid4().hex
						
						items = [x for x in items if x.get("id") != config_id and x.get("source_entity_id") != source_entity]
						entry = {
							"id": config_id,
							"source_entity_id": source_entity,
							"add_amount": add_amount_float,
						}
						if friendly_name:
							entry["name"] = friendly_name
						items.append(entry)
						self._set_price_adjustments(items)
						self._user_defaults["_price_adjust_status"] = f"Added {source_entity}"
						return await self.async_step_price_adjustments()
			
			if action == "remove":
				target_id = user_input.get("price_adjust_remove")
				if not items:
					self._errors["base"] = "no_price_adjustments"
				elif not target_id:
					self._errors["price_adjust_remove"] = "price_adjust_missing_fields"
				else:
					items = [x for x in items if str(x.get("id")) != str(target_id)]
					self._set_price_adjustments(items)
					self._user_defaults["_price_adjust_status"] = "Removed entry"
					return await self.async_step_price_adjustments()
			
			if action == "clear":
				if items:
					items = []
					self._set_price_adjustments(items)
					self._user_defaults["_price_adjust_status"] = "Cleared all entries"
					return await self.async_step_price_adjustments()
				self._errors["base"] = "no_price_adjustments"
		
		schema = {}
		schema[vol.Optional("price_adjust_action", default="add")] = SelectSelector(
			SelectSelectorConfig(
				options=[
					{"value": "add", "label": "Add / update adjustment"},
					{"value": "remove", "label": "Remove adjustment"},
					{"value": "clear", "label": "Remove all"},
					{"value": "finish", "label": "Done"},
				],
				multiple=False,
				mode=SelectSelectorMode.DROPDOWN,
			)
		)
		schema[vol.Optional("price_adjust_source")] = EntitySelector(
			EntitySelectorConfig(domain="sensor", multiple=False)
		)
		schema[vol.Optional("price_adjust_add_amount", default=0.0)] = NumberSelector(
			NumberSelectorConfig(
				min=-10.0,
				max=10.0,
				step=0.001,
				unit_of_measurement="(same as source)",
				mode=NumberSelectorMode.BOX,
			)
		)
		schema[vol.Optional("price_adjust_name")] = TextSelector(TextSelectorConfig(multiline=False))
		if remove_options:
			schema[vol.Optional("price_adjust_remove")] = SelectSelector(
				SelectSelectorConfig(options=remove_options, multiple=False, mode=SelectSelectorMode.DROPDOWN)
			)
		
		return self.async_show_form(
			step_id="price_adjustments",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"price_adjust_summary": price_adjust_summary,
				"price_adjust_status": status_message or "",
			},
		)

	async def async_step_power_sums(self, user_input=None):
		"""Manage derived power sensors that sum multiple source power sensors."""
		self._errors = {}
		items = self._get_power_sums()
		status_message = self._user_defaults.pop("_power_sum_status", None)
		
		summary_lines = []
		for item in items:
			item_id = item.get("id")
			name = (item.get("name") or item_id or "").strip()
			sources = item.get("source_entity_ids") or []
			if not item_id or not sources:
				continue
			summary_lines.append(f"{name} — {len(sources)} sources")
		power_sum_summary = "\n".join(summary_lines) or "— None configured —"
		
		remove_options = []
		for item in items:
			item_id = item.get("id")
			name = (item.get("name") or item_id or "").strip()
			sources = item.get("source_entity_ids") or []
			if not item_id:
				continue
			remove_options.append({"value": str(item_id), "label": f"{name} — {len(sources)} sources"})
		
		if user_input is not None:
			action = user_input.get("power_sum_action", "finish")
			
			if action == "finish":
				next_step = self._power_sum_return_step or "menu"
				self._power_sum_return_step = "menu"
				if next_step == "advanced":
					return await self.async_step_advanced()
				if next_step == "sensors":
					return await self.async_step_sensors()
				return await self.async_step_menu()
			
			if action == "add":
				name = (user_input.get("power_sum_name") or "").strip()
				sources = user_input.get("power_sum_sources") or []
				if not sources or len(sources) < 2:
					self._errors["base"] = "power_sum_missing_fields"
				else:
					# Use stable id derived from name where possible
					if name:
						config_id = name.lower().strip().replace(" ", "_")
					else:
						config_id = uuid.uuid4().hex
					items = [x for x in items if str(x.get("id")) != str(config_id)]
					entry = {
						"id": config_id,
						"source_entity_ids": list(sources),
					}
					if name:
						entry["name"] = name
					items.append(entry)
					self._set_power_sums(items)
					self._user_defaults["_power_sum_status"] = f"Added {name or config_id}"
					return await self.async_step_power_sums()
			
			if action == "remove":
				target_id = user_input.get("power_sum_remove")
				if not items:
					self._errors["base"] = "no_power_sums"
				elif not target_id:
					self._errors["power_sum_remove"] = "power_sum_missing_fields"
				else:
					items = [x for x in items if str(x.get("id")) != str(target_id)]
					self._set_power_sums(items)
					self._user_defaults["_power_sum_status"] = "Removed entry"
					return await self.async_step_power_sums()
			
			if action == "clear":
				if items:
					items = []
					self._set_power_sums(items)
					self._user_defaults["_power_sum_status"] = "Cleared all entries"
					return await self.async_step_power_sums()
				self._errors["base"] = "no_power_sums"
		
		schema = {}
		schema[vol.Optional("power_sum_action", default="add")] = SelectSelector(
			SelectSelectorConfig(
				options=[
					{"value": "add", "label": "Add / update sum"},
					{"value": "remove", "label": "Remove sum"},
					{"value": "clear", "label": "Remove all"},
					{"value": "finish", "label": "Done"},
				],
				multiple=False,
				mode=SelectSelectorMode.DROPDOWN,
			)
		)
		schema[vol.Optional("power_sum_name")] = TextSelector(TextSelectorConfig(multiline=False))
		schema[vol.Optional("power_sum_sources")] = EntitySelector(
			EntitySelectorConfig(domain="sensor", multiple=True)
		)
		if remove_options:
			schema[vol.Optional("power_sum_remove")] = SelectSelector(
				SelectSelectorConfig(options=remove_options, multiple=False, mode=SelectSelectorMode.DROPDOWN)
			)
		
		return self.async_show_form(
			step_id="power_sums",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"power_sum_summary": power_sum_summary,
				"power_sum_status": status_message or "",
			},
		)

	async def async_step_constant_devices(self, user_input=None):
		"""Manage constant power device definitions."""
		self._errors = {}
		devices = self._get_constant_devices()
		status_message = self._user_defaults.pop("_constant_devices_status", None)
		constant_summary = format_constant_power_devices_text(devices) or "— None configured —"
		
		remove_options = []
		for device in devices:
			switch_id = device.get(CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID)
			if not switch_id:
				continue
			power_w = device.get(CONF_CONSTANT_DEVICE_POWER_W)
			instances = device.get(CONF_CONSTANT_DEVICE_INSTANCES, 1)
			try:
				power_display = f"{float(power_w):g}"
			except (TypeError, ValueError):
				power_display = str(power_w)
			try:
				instances_int = int(instances)
			except (TypeError, ValueError):
				instances_int = 1
			if instances_int < 1:
				instances_int = 1
			if instances_int > 1:
				try:
					per_display = f"{(float(power_w) / float(instances_int)):g}"
				except (TypeError, ValueError, ZeroDivisionError):
					per_display = "?"
				power_display = f"{power_display} W x{instances_int} ({per_display} W each)"
			else:
				power_display = f"{power_display} W"
			remove_options.append(
				{
					"value": switch_id,
					"label": f"{device.get(CONF_CONSTANT_DEVICE_NAME) or switch_id} — {power_display}"
				}
			)
		
		if user_input is not None:
			action = user_input.get("constant_device_action", "finish")
			
			if action == "finish":
				self._constant_devices_return_step = self._constant_devices_return_step or "menu"
				next_step = self._constant_devices_return_step
				self._constant_devices_return_step = "menu"
				if next_step == "advanced":
					return await self.async_step_advanced()
				if next_step == "sensors":
					return await self.async_step_sensors()
				return await self.async_step_menu()
			
			if action == "add":
				switch_entity = user_input.get("constant_device_switch")
				power_value = user_input.get("constant_device_power")
				instances_value = user_input.get("constant_device_instances", 1)
				friendly_name = user_input.get("constant_device_name")
				
				if not switch_entity or power_value is None:
					self._errors["base"] = "constant_device_missing_fields"
				else:
					try:
						power_w = float(power_value)
					except (TypeError, ValueError):
						self._errors["base"] = "constant_device_invalid_power"
					else:
						try:
							instances = int(instances_value)
						except (TypeError, ValueError):
							instances = 1
						if instances < 1 or instances > 50:
							self._errors["base"] = "constant_device_invalid_instances"
							return await self.async_step_constant_devices()
						if power_w <= 0:
							self._errors["base"] = "constant_device_invalid_power"
						else:
							devices = [
								d for d in devices
								if d.get(CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID) != switch_entity
							]
							entry = {
								CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID: switch_entity,
								CONF_CONSTANT_DEVICE_POWER_W: power_w,
								CONF_CONSTANT_DEVICE_INSTANCES: instances,
							}
							if friendly_name:
								entry[CONF_CONSTANT_DEVICE_NAME] = friendly_name
							devices.append(entry)
							self._set_constant_devices(devices)
							self._user_defaults["_constant_devices_status"] = f"Added {switch_entity}"
							return await self.async_step_constant_devices()
			
			elif action == "remove":
				target_switch = user_input.get("constant_device_remove")
				if not devices:
					self._errors["base"] = "no_constant_devices"
				elif not target_switch:
					self._errors["constant_device_remove"] = "constant_device_missing_fields"
				else:
					devices = [
						d for d in devices
						if d.get(CONF_CONSTANT_DEVICE_SWITCH_ENTITY_ID) != target_switch
					]
					self._set_constant_devices(devices)
					self._user_defaults["_constant_devices_status"] = f"Removed {target_switch}"
					return await self.async_step_constant_devices()
			
			elif action == "clear":
				if devices:
					devices = []
					self._set_constant_devices(devices)
					self._user_defaults["_constant_devices_status"] = "Cleared all entries"
					return await self.async_step_constant_devices()
				self._errors["base"] = "no_constant_devices"
			else:
				self._errors["base"] = "constant_device_unknown_action"
		
		schema = {}
		schema[vol.Optional("constant_device_action", default="add")] = SelectSelector(
			SelectSelectorConfig(
				options=[
					{"value": "add", "label": "Add / update device"},
					{"value": "remove", "label": "Remove device"},
					{"value": "clear", "label": "Remove all"},
					{"value": "finish", "label": "Done"}
				],
				multiple=False,
				mode=SelectSelectorMode.DROPDOWN
			)
		)
		schema[vol.Optional("constant_device_switch")] = EntitySelector(
			EntitySelectorConfig(
				domain=["switch", "input_boolean"],
				multiple=False
			)
		)
		schema[vol.Optional("constant_device_power", default=3000)] = NumberSelector(
			NumberSelectorConfig(
				min=1,
				max=20000,
				step=10,
				unit_of_measurement="Watts",
				mode=NumberSelectorMode.BOX
			)
		)
		schema[vol.Optional("constant_device_instances", default=1)] = NumberSelector(
			NumberSelectorConfig(
				min=1,
				max=50,
				step=1,
				unit_of_measurement="entities",
				mode=NumberSelectorMode.BOX
			)
		)
		schema[vol.Optional("constant_device_name")] = TextSelector(
			TextSelectorConfig(multiline=False)
		)
		if remove_options:
			schema[vol.Optional("constant_device_remove")] = SelectSelector(
				SelectSelectorConfig(
					options=remove_options,
					multiple=False,
					mode=SelectSelectorMode.DROPDOWN
				)
			)
		
		return self.async_show_form(
			step_id="constant_devices",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"constant_summary": constant_summary,
				"constant_status": status_message or ""
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