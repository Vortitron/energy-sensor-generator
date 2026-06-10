import logging
from datetime import datetime, timedelta
import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
	SensorEntity, 
	SensorDeviceClass,
	SensorStateClass
)
from homeassistant.const import UnitOfEnergy, STATE_ON, STATE_OFF, STATE_OPEN, STATE_CLOSED
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change, async_track_time_interval
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.restore_state import RestoreEntity

_LOGGER = logging.getLogger(__name__)

try:
	from homeassistant.components import recorder
	STATISTICS_AVAILABLE = True
except ImportError:
	STATISTICS_AVAILABLE = False
	_LOGGER.warning("Statistics module not available, using point sampling only")
from .utils import StorageManager, derive_constant_base_name
from .const import (
	DOMAIN, 
	CONF_DEBUG_LOGGING, 
	CONF_USE_STATISTICAL,
	CONF_FORCE_STATISTICAL_ONLY,
	CONF_STAT_LOOKBACK_MINUTES,
	CONF_MAX_ENERGY_PER_HOUR,
	CONF_CONSTANT_POWER_DEVICES,
	CONF_PRICE_ADJUST_SENSORS,
	POINT_SAMPLING_MAX_GAP_SECONDS,
)
import time
from .energy_math import left_riemann_energy, held_power_energy_kwh, MIN_STATISTICAL_WINDOW_SECONDS
from .price_adjustment import compute_adjusted_value, is_price_attribute_key, adjust_attribute_value
from .entity_helpers import (
	is_debug_enabled as _is_debug_enabled,
	debug_log as _debug_log,
	info_log as _info_log,
	get_friendly_name,
	get_friendly_name_from_base,
	get_unique_entity_name,
	persist_storage_key as _persist_storage_key,
)
from .period_sensors import (
	PeriodEnergySensor,
	DailyEnergySensor,
	MonthlyEnergySensor,
	WeeklyEnergySensor,
	AnnualEnergySensor,
)

def _get_config_options(hass: HomeAssistant) -> dict:
	"""Get configuration options from the integration."""
	default_options = {
		CONF_USE_STATISTICAL: True,  # Use statistical calculation by default
		CONF_FORCE_STATISTICAL_ONLY: False,
		CONF_STAT_LOOKBACK_MINUTES: 30,
		CONF_MAX_ENERGY_PER_HOUR: 0,  # 0 = disabled (no limit)
	}
	
	# Check all hass.data domain entries safely (some keys are floats used for throttling)
	for _, entry_data in hass.data.get(DOMAIN, {}).items():
		try:
			if isinstance(entry_data, dict) and "options" in entry_data and isinstance(entry_data["options"], dict):
				return {**default_options, **entry_data["options"]}
		except Exception:
			continue
	
	# Also check direct config entries
	for config_entry in hass.config_entries.async_entries(DOMAIN):
		if config_entry.options:
			return {**default_options, **config_entry.options}
	
	return default_options

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
	"""Set up the sensor platform."""
	# Store the async_add_entities callback for later use by generate_sensors_service
	if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
		hass.data[DOMAIN][entry.entry_id]["async_add_entities"] = async_add_entities
	
	# Check if we need to recreate existing entities during reload
	options = entry.options
	
	# Only proceed if we have selected sensors configured
	selected_sensors = options.get("selected_power_sensors", []) or []
	constant_devices = options.get(CONF_CONSTANT_POWER_DEVICES, []) or []
	price_adjustments = options.get(CONF_PRICE_ADJUST_SENSORS, []) or []
	_LOGGER.info(f"Setting up energy sensors for selected power sensors: {selected_sensors}")
	_LOGGER.info(f"Setting up constant power devices: {constant_devices}")
	_LOGGER.info(f"Setting up price adjustment sensors: {len(price_adjustments)} configured")
	if not selected_sensors and not constant_devices and not price_adjustments:
		_LOGGER.warning("No power sensors, constant devices, or price adjustments configured, skipping sensor setup")
		return
	constant_device_map = {}
	for device in constant_devices:
		try:
			base_name = derive_constant_base_name(device)
			constant_device_map[base_name] = device
		except Exception as err:
			_LOGGER.error(f"Failed to prepare constant power device {device}: {err}")
	
	# Find existing generated sensors
	entity_registry = er.async_get(hass)
	existing_entities = []
	
	# Look for entities with this integration's platform
	for entity_id, entity_entry in entity_registry.entities.items():
		if entity_entry.platform == DOMAIN and entity_entry.config_entry_id == entry.entry_id:
			existing_entities.append((entity_id, entity_entry.unique_id))
	
	# If we have existing entities, recreate them to ensure they're properly linked
	# But only if we have selected sensors configured
	if existing_entities and (selected_sensors or constant_device_map):
		_LOGGER.info(f"Found {len(existing_entities)} existing energy sensors to recreate during setup")
		
		# Get storage manager - retrieve from the integration's data
		storage_manager = None
		if DOMAIN in hass.data:
			for entry_data in hass.data[DOMAIN].values():
				if isinstance(entry_data, dict) and "storage_manager" in entry_data:
					storage_manager = entry_data["storage_manager"]
					break
		
		if not storage_manager:
			# Fallback: create a storage manager for this entry
			storage_manager = StorageManager(hass)
		
		# Group entities by base name
		entities_by_base = {}
		for entity_id, unique_id in existing_entities:
			# Extract base name from unique_id
			if "_daily_energy" in unique_id:
				base_name = unique_id.replace("_daily_energy", "")
				sensor_type = "daily"
			elif "_monthly_energy" in unique_id:
				base_name = unique_id.replace("_monthly_energy", "")
				sensor_type = "monthly"
			elif "_weekly_energy" in unique_id:
				base_name = unique_id.replace("_weekly_energy", "")
				sensor_type = "weekly"
			elif "_annual_energy" in unique_id:
				base_name = unique_id.replace("_annual_energy", "")
				sensor_type = "annual"
			else:
				base_name = unique_id.replace("_energy", "")
				sensor_type = "main"
			
			if base_name not in entities_by_base:
				entities_by_base[base_name] = {}
			entities_by_base[base_name][sensor_type] = entity_id
		
		# Recreate entities
		entities_to_add = []
		
		for base_name, sensor_types in entities_by_base.items():
			# Determine source sensor from base name
			constant_config = constant_device_map.get(base_name)
			expected_source_sensor = f"sensor.{base_name}_power"
			source_sensor = constant_config.get("switch_entity_id") if constant_config else expected_source_sensor
			custom_name = constant_config.get("name") if constant_config else None
			
			# Debug the mapping process
			_LOGGER.debug(f"Recreating sensors for base_name '{base_name}', expected source: '{expected_source_sensor}'")
			_LOGGER.debug(f"Selected sensors: {selected_sensors}")
			
			# Verify the expected source sensor is in the selected list
			if not constant_config and expected_source_sensor not in selected_sensors:
				_LOGGER.warning(f"Expected source sensor '{expected_source_sensor}' not found in selected sensors for {base_name}")
				# Try to find the actual source sensor from selected sensors
				found_source = None
				for selected in selected_sensors:
					selected_base = selected.replace("sensor.", "").replace("_power", "")
					if selected_base == base_name:
						found_source = selected
						break
				
				if found_source:
					source_sensor = found_source
					_LOGGER.info(f"Mapped {base_name} to source sensor: {source_sensor}")
				else:
					_LOGGER.error(f"Cannot find appropriate source sensor for {base_name}. Expected: {expected_source_sensor}, Available: {selected_sensors}")
					# Skip this entity group if we can't find the source
					continue
			
			# Check if source sensor still exists
			if hass.states.get(source_sensor) is None:
				_LOGGER.warning(f"Source sensor {source_sensor} not yet available during startup for {base_name}")
				# During startup, we'll proceed anyway - the sensor should handle unavailable source gracefully
			else:
				if not constant_config:
					# Validate that this is actually a power sensor
					source_state = hass.states.get(source_sensor)
					unit = source_state.attributes.get("unit_of_measurement", "")
					device_class = source_state.attributes.get("device_class", "")
					if unit in ["kWh", "kwh"] or device_class == "energy":
						_LOGGER.error(f"CRITICAL ERROR during startup: Source sensor {source_sensor} for {base_name} is an ENERGY sensor (unit: {unit}, device_class: {device_class}) instead of a POWER sensor. Skipping recreation.")
						continue
					else:
						_LOGGER.debug(f"Validated source sensor {source_sensor} is a power sensor (unit: {unit}, device_class: {device_class})")
				else:
					_LOGGER.debug(f"Validated constant switch {source_sensor} for {base_name}")
			
			# Get device identifiers for proper device grouping
			device_identifiers = None
			source_entity = entity_registry.async_get(source_sensor)
			if source_entity and source_entity.device_id:
				device_registry = dr.async_get(hass)
				device = device_registry.async_get(source_entity.device_id)
				if device:
					device_identifiers = device.identifiers
			
			# Recreate main energy sensor if it exists
			if "main" in sensor_types:
				# Double-check we're not creating energy sensor from energy source
				if source_sensor.endswith("_energy"):
					_LOGGER.error(f"PREVENTING INFINITE LOOP: Refusing to create energy sensor from energy source {source_sensor} for {base_name}")
					continue
				
				energy_sensor = EnergySensor(hass, base_name, source_sensor, storage_manager, device_identifiers, custom_name, constant_config)
				entities_to_add.append(energy_sensor)
				_LOGGER.debug(f"Recreated main energy sensor for {base_name} with source {source_sensor}")
			
			# Recreate daily sensor if it exists
			if "daily" in sensor_types:
				daily_sensor = DailyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_manager, device_identifiers)
				entities_to_add.append(daily_sensor)
				_LOGGER.debug(f"Recreated daily energy sensor for {base_name}")
			
			# Recreate monthly sensor if it exists  
			if "monthly" in sensor_types:
				monthly_sensor = MonthlyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_manager, device_identifiers)
				entities_to_add.append(monthly_sensor)
				_LOGGER.debug(f"Recreated monthly energy sensor for {base_name}")
			# Recreate weekly sensor if it exists
			if "weekly" in sensor_types:
				weekly_sensor = WeeklyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_manager, device_identifiers)
				entities_to_add.append(weekly_sensor)
				_LOGGER.debug(f"Recreated weekly energy sensor for {base_name}")
			# Recreate annual sensor if it exists
			if "annual" in sensor_types:
				annual_sensor = AnnualEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_manager, device_identifiers)
				entities_to_add.append(annual_sensor)
				_LOGGER.debug(f"Recreated annual energy sensor for {base_name}")
		
		# Add all recreated entities
		if entities_to_add:
			async_add_entities(entities_to_add, True)  # True = update_before_add
			_LOGGER.info(f"Successfully recreated {len(entities_to_add)} energy sensors during setup")
	
	# Always create price adjustment sensors from options (they're stateless / derived)
	price_entities = []
	if price_adjustments:
		for item in price_adjustments:
			try:
				price_entities.append(PriceAdjustedSensor(hass, entry.entry_id, dict(item)))
			except Exception as err:
				_LOGGER.error(f"Failed to create price adjustment sensor from {item}: {err}")
		if price_entities:
			async_add_entities(price_entities, True)
	return


def _get_device_identifiers_for_entity(hass: HomeAssistant, entity_id: str):
	"""Return device identifiers for a given entity, if it belongs to a device."""
	try:
		entity_registry = er.async_get(hass)
		entry = entity_registry.async_get(entity_id)
		if not entry or not entry.device_id:
			return None
		device_registry = dr.async_get(hass)
		device = device_registry.async_get(entry.device_id)
		if not device:
			return None
		return device.identifiers
	except Exception:
		return None


def _get_config_entry_for_id(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
	"""Find a config entry by entry_id for this domain."""
	try:
		for cfg in hass.config_entries.async_entries(DOMAIN):
			if cfg.entry_id == entry_id:
				return cfg
	except Exception:
		return None
	return None


class PriceAdjustedSensor(SensorEntity):
	"""Sensor that mirrors another sensor and adds a fixed amount."""

	def __init__(self, hass: HomeAssistant, entry_id: str, config: dict):
		self._hass = hass
		self._entry_id = entry_id

		self._config_id = str(config.get("id") or "").strip()
		self._source_entity_id = str(config.get("source_entity_id") or "").strip()
		self._name_override = (config.get("name") or "").strip()

		try:
			self._default_add_amount = float(config.get("add_amount", 0))
		except (TypeError, ValueError):
			self._default_add_amount = 0.0

		if not self._config_id:
			raise ValueError("Missing price adjustment config id")
		if not self._source_entity_id:
			raise ValueError("Missing source_entity_id")

		self._attr_unique_id = f"price_adjust_{self._config_id}"

		source_name = get_friendly_name(hass, self._source_entity_id)
		proposed_name = self._name_override or f"{source_name} (Adjusted)"
		self._attr_name = get_unique_entity_name(hass, proposed_name)

		self._attr_state_class = SensorStateClass.MEASUREMENT
		self._attr_icon = "mdi:cash-plus"
		self._attr_entity_registry_enabled_default = True

		device_identifiers = _get_device_identifiers_for_entity(hass, self._source_entity_id)
		if device_identifiers:
			self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
		else:
			self._attr_device_info = DeviceInfo(
				identifiers={(DOMAIN, "price_adjustments")},
				name="Price Adjustments",
				manufacturer="Energy Sensor Generator",
				model="Adjusted price sensor",
			)

		self._native_value = None
		self._unit = None
		self._available = True
		self._copied_attributes = {}
		self._unsub_state_change = None

	def _get_current_add_amount(self) -> float:
		"""Read current add_amount from the latest config entry options."""
		entry = _get_config_entry_for_id(self._hass, self._entry_id)
		if not entry:
			return self._default_add_amount
		items = entry.options.get(CONF_PRICE_ADJUST_SENSORS, []) or []
		for item in items:
			if str(item.get("id") or "") == self._config_id:
				try:
					return float(item.get("add_amount", self._default_add_amount))
				except (TypeError, ValueError):
					return self._default_add_amount
		return self._default_add_amount

	def _recalculate_from_source(self):
		state = self._hass.states.get(self._source_entity_id)
		if not state or state.state in ("unknown", "unavailable"):
			self._native_value = None
			self._available = False
			self._copied_attributes = {}
			return
		self._available = True
		self._unit = state.attributes.get("unit_of_measurement")
		add_amount = self._get_current_add_amount()
		self._native_value = compute_adjusted_value(state.state, add_amount)
		
		# Copy all attributes, adjusting only price-like ones (except raw*)
		copied = dict(state.attributes or {})
		adjusted_attrs = {}
		for key, value in copied.items():
			key_norm = str(key)
			if "raw" in key_norm.lower():
				adjusted_attrs[key] = value
				continue
			if is_price_attribute_key(key_norm):
				adjusted_attrs[key] = adjust_attribute_value(value, add_amount)
				continue
			# Non-price attributes are copied verbatim
			adjusted_attrs[key] = value
		
		# Always expose metadata for debugging / template usage
		adjusted_attrs["source_entity_id"] = self._source_entity_id
		adjusted_attrs["add_amount"] = add_amount
		self._copied_attributes = adjusted_attrs

	async def async_added_to_hass(self):
		self._recalculate_from_source()
		self._unsub_state_change = async_track_state_change_event(
			self._hass, [self._source_entity_id], self._handle_source_change
		)
		self.async_write_ha_state()

	async def async_will_remove_from_hass(self):
		if self._unsub_state_change:
			try:
				self._unsub_state_change()
			except Exception:
				pass
			self._unsub_state_change = None

	async def _handle_source_change(self, event):
		self._recalculate_from_source()
		self.async_write_ha_state()

	@property
	def available(self):
		return self._available

	@property
	def native_value(self):
		return self._native_value

	@property
	def native_unit_of_measurement(self):
		return self._unit

	@property
	def unit_of_measurement(self):
		return self._unit

	async def async_update(self):
		"""Force a refresh (e.g. after options change)."""
		self._recalculate_from_source()

	@property
	def extra_state_attributes(self):
		return dict(self._copied_attributes or {})

class EnergySensor(SensorEntity, RestoreEntity):
	"""Custom sensor to calculate kWh from power (Watts)."""

	def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None, friendly_name_override=None, constant_config=None):
		"""Initialize the sensor."""
		self._hass = hass
		self._base_name = base_name
		self._source_sensor = source_sensor
		# Backwards compat: storage_path param may be Path; but prefer StorageManager
		self._storage_manager: StorageManager | None = storage_path if isinstance(storage_path, StorageManager) else None
		self._storage_path = storage_path  # kept for type compatibility
		self._device_identifiers = device_identifiers
		self._constant_power_w = None
		self._constant_config = None
		if constant_config:
			try:
				power_value = float(constant_config.get("power_w", 0))
			except (TypeError, ValueError):
				power_value = 0
			if power_value > 0:
				self._constant_power_w = power_value
				self._constant_config = {
					"power_w": power_value,
					"switch_entity_id": constant_config.get("switch_entity_id", source_sensor),
					"name": constant_config.get("name")
				}
			else:
				_LOGGER.error(f"Invalid constant power value for {base_name}: {constant_config.get('power_w')}")
		
		# Validate that we're not creating an energy sensor from an energy source
		source_state = hass.states.get(source_sensor)
		if source_state and self._constant_power_w is None:
			unit = source_state.attributes.get("unit_of_measurement", "")
			device_class = source_state.attributes.get("device_class", "")
			if unit in ["kWh", "kwh"] or device_class == "energy":
				_LOGGER.error(f"CONFIGURATION ERROR: Cannot create energy sensor from energy source '{source_sensor}' (unit: {unit}, device_class: {device_class}). Energy sensors must be created from POWER sensors with unit 'W' or 'kW'. Please reconfigure this integration to monitor power sensors instead.")
				# Don't raise an exception to avoid breaking startup, but log the error clearly
		
		# Get friendly name from the source sensor
		friendly_name = friendly_name_override or get_friendly_name(hass, source_sensor)
		
		# Generate unique entity name to avoid conflicts
		proposed_name = f"{friendly_name} Energy"
		unique_name = get_unique_entity_name(hass, proposed_name)

		# Sensor attributes
		self._attr_name = unique_name
		# Support disambiguated bases like "smart_plug_energy_2" by not appending
		# another "_energy" suffix to the unique_id/entity_id base.
		if base_name.endswith("_energy") or "_energy_" in base_name:
			self._attr_unique_id = base_name
		else:
			self._attr_unique_id = f"{base_name}_energy"
		self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_device_class = SensorDeviceClass.ENERGY
		self._attr_state_class = SensorStateClass.TOTAL_INCREASING
		self._attr_icon = "mdi:flash"
		self._attr_entity_registry_enabled_default = True  # Ensure sensors are enabled by default
		
		# Device info for grouping
		if device_identifiers:
			self._attr_device_info = DeviceInfo(
				identifiers=device_identifiers,
			)
		else:
			# Fallback device info
			self._attr_device_info = DeviceInfo(
				identifiers={(DOMAIN, base_name)},
				name=f"{base_name.replace('_', ' ').title()}",
				manufacturer="Energy Sensor Generator",
				model="Generated Energy Sensor",
			)
		
		# Conversion factor for power units (will be detected at runtime)
		self._power_to_kw_factor = 1000 if self._constant_power_w is not None else None
		
		self._state = 0.0
		self._last_power = None
		self._last_update = None
		self._min_calculation_interval = 1.0  # Minimum seconds between calculations
		self._storage_key = f"{base_name}_energy"
		self._interval_tracker = None
		self._calculating_energy = False  # Flag to prevent concurrent calculations
		self._calculation_count = 0  # Counter for logging frequency
		self._first_calculation_logged = False  # Flag to log first successful calculation
		self._using_statistical = False  # Track which calculation method was last used
		self._last_statistical_calculation = None  # Track when we last performed statistical calculation
		self._last_save_ts = 0
		# Energy accumulated from source state changes since the last interval
		# calculation (left Riemann). Consumed by point sampling, discarded when
		# a statistical calculation covers the same window.
		self._pending_point_energy = 0.0
		# State will be loaded in async_added_to_hass

	def _get_power_conversion_factor(self, hass, source_sensor):
		"""Determine the conversion factor from source power unit to kW."""
		if self._constant_power_w is not None:
			return 1000
		try:
			# Get the source sensor state and attributes
			state = hass.states.get(source_sensor)
			if not state:
				# Return None if we can't determine the unit yet - will retry later
				_LOGGER.debug(f"UNIT DETECTION: Could not get state for {source_sensor}, will retry when available")
				return None
				
			# Check unit of measurement
			unit = state.attributes.get("unit_of_measurement", "").strip()
			device_class = state.attributes.get("device_class", "")
			
			_LOGGER.info(f"UNIT DETECTION: {source_sensor} | unit='{unit}' | device_class='{device_class}' | state={state.state}")
			
			# Normalise unit to lowercase for comparison
			unit_lower = unit.lower()
			
			if unit_lower in ["kw", "kilowatt", "kilowatts"]:
				# Source is already in kW, no conversion needed
				_LOGGER.info(f"Power unit detected for {source_sensor}: kW (conversion factor: 1) - Current value: {state.state}")
				return 1
			elif unit_lower in ["w", "watt", "watts"]:
				# Source is in Watts, need to divide by 1000 to get kW
				_LOGGER.info(f"Power unit detected for {source_sensor}: W (conversion factor: 1000) - Current value: {state.state}")
				return 1000
			else:
				# Unknown or missing unit, assume Watts for backwards compatibility
				_LOGGER.warning(f"Unknown/missing unit for {source_sensor} ('{unit}'), assuming Watts (conversion factor: 1000) - Current value: {state.state}")
				return 1000
		except Exception as e:
			_LOGGER.error(f"Error determining power conversion factor for {source_sensor}: {e}")
			return None

	def _state_to_power(self, state):
		"""Convert a HA state object into a numeric power reading."""
		if self._constant_power_w is not None:
			if not state:
				return 0.0
			value = str(state.state).lower()
			if value in ("", "unknown", "unavailable"):
				return 0.0
			if value in (STATE_ON, STATE_OPEN, "true", "1"):
				return self._constant_power_w
			if value in (STATE_OFF, STATE_CLOSED, "false", "0"):
				return 0.0
			try:
				numeric = float(state.state)
				return self._constant_power_w if numeric > 0 else 0.0
			except (ValueError, TypeError):
				_debug_log(self._hass, f"Constant device {self._attr_name} received non-numeric switch state '{state.state}', treating as OFF")
				return 0.0
		try:
			return float(state.state)
		except (ValueError, TypeError):
			return None

	def _ensure_conversion_factor(self):
		"""Ensure the power conversion factor is set."""
		if self._power_to_kw_factor is None:
			self._power_to_kw_factor = self._get_power_conversion_factor(self._hass, self._source_sensor)
			if self._power_to_kw_factor is not None:
				unit_name = "kW" if self._power_to_kw_factor == 1 else "W"
				_LOGGER.info(f"Conversion factor set for {self._source_sensor}: {unit_name} -> {self._attr_name} (factor: {self._power_to_kw_factor})")
			else:
				_LOGGER.debug(f"CONVERSION FACTOR: Source sensor {self._source_sensor} not yet available, will retry")
		# Note: Factor is now set during _load_state() to enable data migration

	async def _get_statistical_power_data(self, start_time, end_time):
		"""Calculate energy using left Riemann sum (same method as Home Assistant's integration sensor)."""
		try:
			if not STATISTICS_AVAILABLE:
				_debug_log(self.hass, f"Statistics module not available for {self._attr_name}")
				return None
				
			recorder_instance = recorder.get_instance(self.hass)
			if not recorder_instance:
				_debug_log(self.hass, f"Recorder not available for {self._attr_name}")
				return None
				
			# Ensure we have a reasonable time range for a reliable calculation
			time_delta = (end_time - start_time).total_seconds()
			if time_delta < MIN_STATISTICAL_WINDOW_SECONDS:
				_debug_log(self.hass, f"Time range too short for statistical calculation ({time_delta:.1f}s) for {self._attr_name}")
				return None
			
			# Import required modules for history access
			from homeassistant.components.recorder import history
			
			_debug_log(self.hass, f"Attempting statistical calculation for {self._attr_name} from {start_time} to {end_time} (duration: {time_delta:.1f}s)")
			
			# Get conversion factor before entering executor
			conversion_factor = self._power_to_kw_factor
			if not conversion_factor or conversion_factor <= 0:
				_debug_log(self.hass, f"Invalid conversion factor ({conversion_factor}) for statistical calculation")
				return None
			
			def _get_history_data():
				"""Get historical states using the recorder history API (runs in executor)."""
				try:
					# Get historical states for the power sensor
					historical_states = history.get_significant_states(
						self.hass,
						start_time,
						end_time,
						entity_ids=[self._source_sensor],
						minimal_response=False,
						significant_changes_only=False
					)
					
					if not historical_states or self._source_sensor not in historical_states:
						return {"error": "No historical data found"}
						
					states_list = historical_states[self._source_sensor]
					
					# Filter out invalid states and convert to (power, time) samples
					samples = []
					for state in states_list:
						try:
							if state.state not in ("unknown", "unavailable", None):
								power_value = float(state.state)
								if power_value >= 0:  # Only accept non-negative power values
									samples.append((power_value, state.last_updated))
						except (ValueError, TypeError, AttributeError):
							continue
					
					# A single sample is enough: get_significant_states includes the
					# state as of start_time, and the final segment up to end_time
					# is integrated by left_riemann_energy.
					if not samples:
						return {"error": f"No valid states from {len(states_list)} total - need at least 1 valid data point"}
					
					# LEFT Riemann sum (like Home Assistant's integration sensor),
					# including the final segment up to end_time so consecutive
					# windows tile without losing energy.
					result = left_riemann_energy(samples, end_time, conversion_factor)
					
					return {
						"total_energy": result["total_energy"],
						"segments": result["segments"],
						"total_states": len(states_list),
						"valid_states": len(samples),
						"max_power": result["max_power"],
						"min_power": result["min_power"],
						"avg_power": result["avg_power"],
					}
					
				except Exception as e:
					return {"error": f"Exception in calculation: {str(e)}"}
			
			# Use the recorder's async executor
			statistical_data = await recorder_instance.async_add_executor_job(_get_history_data)
			
			if statistical_data.get("error"):
				error_msg = statistical_data['error']
				
				# For the common "not enough states" error, provide more context and reduce spam
				if "Not enough" in error_msg:
					# Only log this error occasionally to avoid spam (every 30 minutes for frequent sensors)
					current_time = time.time()
					last_logged_key = f"_insufficient_data_last_log_{self._attr_name}"
					if DOMAIN not in self.hass.data:
						return None

					if last_logged_key not in self.hass.data[DOMAIN]:
						self.hass.data[DOMAIN][last_logged_key] = 0

					# Only log this error every 30 minutes to avoid spam for frequent sensors
					if current_time - self.hass.data[DOMAIN][last_logged_key] > 1800:
						_debug_log(self.hass, f"Statistical calculation for {self._attr_name}: {error_msg} - This is normal for new sensors or when data is sparse")
						self.hass.data[DOMAIN][last_logged_key] = current_time
				else:
					# For other errors, log normally but with less frequency
					current_time = time.time()
					error_logged_key = f"_stat_error_last_log_{self._attr_name}"
					if DOMAIN not in self.hass.data:
						return None

					if error_logged_key not in self.hass.data[DOMAIN]:
						self.hass.data[DOMAIN][error_logged_key] = 0

					if current_time - self.hass.data[DOMAIN][error_logged_key] > 300:  # Every 5 minutes
						_debug_log(self.hass, f"Error in statistical calculation for {self._attr_name}: {error_msg}")
						self.hass.data[DOMAIN][error_logged_key] = current_time
				
				return None
			
			statistical_energy = statistical_data["total_energy"]
			calculation_count = statistical_data["segments"]
			max_power = statistical_data.get("max_power", 0)
			avg_power = statistical_data.get("avg_power", 0)
			
			if statistical_energy is not None and statistical_energy >= 0:
				_debug_log(self.hass, f"Statistical calculation successful for {self._attr_name}: {statistical_energy:.8f}kWh over {time_delta:.1f}s")
				_debug_log(self.hass, f"  Found {statistical_data['total_states']} states, {statistical_data['valid_states']} valid, {calculation_count} segments")
				_debug_log(self.hass, f"  Power range: {statistical_data.get('min_power', 0):.2f}W to {max_power:.2f}W, avg: {avg_power:.2f}W")
				return statistical_energy
			else:
				_debug_log(self.hass, f"Statistical calculation returned no energy for {self._attr_name}")
				return None
				
		except Exception as e:
			_debug_log(self.hass, f"Error in statistical calculation for {self._attr_name}: {str(e)}")
			import traceback
			_debug_log(self.hass, f"Traceback: {traceback.format_exc()}")
			return None

	async def _load_state(self):
		"""Load state from storage."""
		if self._storage_manager:
			storage = await self._storage_manager.async_load()
		else:
			from homeassistant.helpers import storage as ha_storage
			store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
			storage = await store.async_load() or {}
		state_data = storage.get(self._storage_key, {})
		
		if isinstance(state_data, dict):
			self._state = state_data.get("value", 0.0)
			last_power = state_data.get("last_power")
			last_update = state_data.get("last_update")
			last_statistical_calculation = state_data.get("last_statistical_calculation")
			stored_conversion_factor = state_data.get("conversion_factor")
			
			# Determine current conversion factor
			current_conversion_factor = self._get_power_conversion_factor(self._hass, self._source_sensor)
			
			# Set the current conversion factor (may be None if source sensor not available yet)
			self._power_to_kw_factor = current_conversion_factor
			
			if last_power is not None:
				self._last_power = last_power
			
			if last_update:
				try:
					parsed_dt = datetime.fromisoformat(last_update)
					# If the datetime is timezone-naive, make it timezone-aware
					if parsed_dt.tzinfo is None:
						self._last_update = dt_util.as_utc(parsed_dt)
					else:
						self._last_update = parsed_dt
				except (ValueError, TypeError):
					self._last_update = None
					
			if last_statistical_calculation:
				try:
					parsed_dt = datetime.fromisoformat(last_statistical_calculation)
					# If the datetime is timezone-naive, make it timezone-aware
					if parsed_dt.tzinfo is None:
						self._last_statistical_calculation = dt_util.as_utc(parsed_dt)
					else:
						self._last_statistical_calculation = parsed_dt
				except (ValueError, TypeError):
					self._last_statistical_calculation = None
		else:
			# Legacy format where state_data is just a float
			legacy_value = float(state_data) if state_data else 0.0
			current_conversion_factor = self._get_power_conversion_factor(self._hass, self._source_sensor)
			
			self._state = legacy_value
			self._power_to_kw_factor = current_conversion_factor

	async def _save_state(self):
		"""Save state to storage."""
		payload = {
			"value": self._state,
			"last_power": self._last_power,
			"last_update": self._last_update.isoformat() if self._last_update else None,
			"last_statistical_calculation": self._last_statistical_calculation.isoformat() if self._last_statistical_calculation else None,
			"conversion_factor": self._power_to_kw_factor
		}
		await _persist_storage_key(self._hass, self._storage_manager, self._storage_key, payload, self._attr_name)

	async def _save_state_throttled(self, min_interval: int = 30):
		"""Save state if at least min_interval seconds passed since last save."""
		now_ts = time.time()
		if (now_ts - self._last_save_ts) < min_interval:
			return
		self._last_save_ts = now_ts
		await self._save_state()

	async def async_added_to_hass(self):
		"""Handle entity addition."""
		# Load state from storage first
		await self._load_state()
		# Try HA restore cache if storage empty
		if self._state == 0.0:
			last = await self.async_get_last_state()
			try:
				if last and last.state not in ("unknown", "unavailable", None):
					self._state = float(last.state)
			except (ValueError, TypeError):
				pass
		
		# Track state changes to the power sensor
		self._unsub_state_change = async_track_state_change_event(
			self._hass, [self._source_sensor], self._handle_state_change
		)
		
		# Get sampling interval from options
		sample_interval = 60  # Default 60 seconds if not specified
		
		# Try to get the configured sample interval from the integration's options
		for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
			try:
				options = entry_data.get("options") if isinstance(entry_data, dict) else None
				if isinstance(options, dict):
					sample_interval = options.get("sample_interval", 60)
					break
			except Exception:
				pass
		
		_LOGGER.debug(f"Setting up energy calculation with {sample_interval} second interval for {self._attr_name}")
		
		# Initialise power tracking if not already set (e.g., on first startup or reload)
		if self._last_power is None or self._last_update is None:
			state = self._hass.states.get(self._source_sensor)
			power = self._state_to_power(state) if state else None
			if power is not None:
				try:
					self._last_power = power
					self._last_update = dt_util.utcnow()
					await self._save_state()
					# Ensure conversion factor is set for logging
					self._ensure_conversion_factor()
					unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
					_LOGGER.debug(f"Initialised {self._attr_name} with current power: {power}{unit_display}")
					self.safe_write_ha_state()
				except Exception as e:
					_LOGGER.error(f"Error saving state during initialization for {self._attr_name}: {e}", exc_info=True)
					# Still set the values even if saving fails
					self._last_power = power
					self._last_update = dt_util.utcnow()
			else:
				# Source sensor not yet available - this is normal during startup
				_debug_log(self._hass, f"Source sensor {self._source_sensor} not yet available during startup for {self._base_name}")
		
		# Set up regular sampling interval for reliable energy calculation
		self._interval_tracker = async_track_time_interval(
			self._hass,
			self._handle_interval_update,
			timedelta(seconds=sample_interval)
		)
		
		# If interval is a multiple of 60 seconds, align updates to wall-clock minutes
		if sample_interval % 60 == 0:
			try:
				# Cancel the non-aligned interval tracker
				if self._interval_tracker:
					self._interval_tracker()
			except Exception:
				pass
			step_minutes = max(1, int(sample_interval // 60))
			self._interval_tracker = async_track_time_change(
				self._hass,
				self._handle_interval_update,
				minute=list(range(0, 60, step_minutes)),
				second=0
			)
		
		# Also set up a midnight update to ensure we get regular updates
		self._unsub_midnight_update = async_track_time_change(
			self._hass,
			self._handle_midnight_update,
			hour=0,
			minute=0,
			second=0
		)
		self.safe_write_ha_state()

	async def _handle_interval_update(self, now):
		"""Update energy calculation at regular intervals using statistical data when possible."""
		# Only log interval updates when they result in actual calculations to reduce spam
		
		if self._calculating_energy:
			_debug_log(self.hass, f"Already calculating energy for {self._attr_name}, skipping")
			return
			
		self._calculating_energy = True
		try:
			# Ensure conversion factor is set
			self._ensure_conversion_factor()
			
			# Safety check for conversion factor
			if not self._power_to_kw_factor or self._power_to_kw_factor <= 0:
				_debug_log(self.hass, f"Conversion factor not yet available for {self._source_sensor}, skipping calculation")
				return
			
			# Get configuration options
			config_options = _get_config_options(self.hass)
			use_statistical = config_options.get(CONF_USE_STATISTICAL, True)
			is_constant_source = self._constant_power_w is not None
			if is_constant_source:
				use_statistical = False
			
			_debug_log(self.hass, f"Configuration: statistical={use_statistical}, stats_available={STATISTICS_AVAILABLE}")
			
			sample_interval = int(config_options.get("sample_interval", 60) or 60)
			max_gap_seconds = max(POINT_SAMPLING_MAX_GAP_SECONDS, sample_interval * 3)
			
			# Try statistical calculation first if enabled.
			# The anchor is where the previous calculation window ended; windows
			# must tile exactly so no energy is counted twice or dropped.
			statistical_data = None
			if use_statistical and STATISTICS_AVAILABLE:
				try:
					lookback_minutes = max(10, int(config_options.get(CONF_STAT_LOOKBACK_MINUTES, 30)))
					anchor = self._last_statistical_calculation or self._last_update

					if anchor:
						gap_seconds = (now - anchor).total_seconds()
						if gap_seconds < MIN_STATISTICAL_WINDOW_SECONDS:
							# Rapid successive call; leave the anchor untouched so the
							# next interval covers this period instead.
							_debug_log(self.hass, f"Skipping statistical calculation - window too short ({gap_seconds:.1f}s)")
						elif gap_seconds > max_gap_seconds:
							# Restart or offline source: do NOT bridge the downtime.
							# Bridging would hold the last pre-gap power across the
							# whole gap and could fabricate energy. Restart the
							# window from now and only count fresh data.
							_LOGGER.info(
								"Skipping energy calculation for %s across a %.0fs gap (restart/offline); "
								"resuming from fresh data to avoid phantom energy.",
								self._attr_name,
								gap_seconds,
							)
							self._last_statistical_calculation = now
						else:
							_debug_log(self.hass, f"Incremental statistical calculation: {anchor} to {now}")
							statistical_data = await self._get_statistical_power_data(anchor, now)
							if statistical_data is not None:
								self._last_statistical_calculation = now
					else:
						# True first calculation: no previous tracking at all.
						# Use the lookback window; this intentionally adds recent
						# historical energy for a brand-new sensor.
						stat_start_time = now - timedelta(minutes=lookback_minutes)
						_info_log(self.hass, f"First calculation for {self._attr_name} using {lookback_minutes}min lookback - this adds recent historical energy", force=True)
						statistical_data = await self._get_statistical_power_data(stat_start_time, now)
						if statistical_data is not None:
							self._last_statistical_calculation = now

					if statistical_data is not None:
						_debug_log(self.hass, f"Statistical calculation successful for {self._attr_name}: {statistical_data:.8f}kWh")
					else:
						_debug_log(self.hass, f"Statistical calculation unavailable for {self._attr_name}")
				except Exception as e:
					_debug_log(self.hass, f"Exception during statistical calculation: {str(e)}")
					statistical_data = None
				
			state = self._hass.states.get(self._source_sensor)
			if not state:
				_debug_log(self.hass, f"Source sensor {self._source_sensor} not found for {self._attr_name}")
				return
				
			if state.state in ("unknown", "unavailable") and not is_constant_source:
				_debug_log(self.hass, f"Source sensor {self._source_sensor} has invalid state '{state.state}' for {self._attr_name}")
				return
				
			power = self._state_to_power(state)
			if power is None:
				_debug_log(self.hass, f"Invalid power value '{state.state}' from {self._source_sensor} for {self._attr_name}")
				return
			
			# Log when we're actually starting calculations
			_debug_log(self.hass, f"Interval update called for {self._attr_name} - power: {power}W, source: {self._source_sensor}")
			
			# Add diagnostic information about the source sensor
			if not is_constant_source:
				source_state = self._hass.states.get(self._source_sensor)
				if source_state:
					unit = source_state.attributes.get("unit_of_measurement", "unknown")
					device_class = source_state.attributes.get("device_class", "unknown")
					_debug_log(self.hass, f"Source sensor details: {self._source_sensor} = {power}{unit} (device_class: {device_class})")
					# Check if this is incorrectly monitoring an energy sensor instead of power sensor
					if unit in ("kWh", "kwh") or device_class == "energy":
						_LOGGER.warning(f"CONFIGURATION ERROR: {self._attr_name} is monitoring an ENERGY sensor ({self._source_sensor}) instead of a POWER sensor! This will not work correctly. Please reconfigure to monitor a power sensor with unit 'W' or 'kW'.")
						return
			
			# Check if we're in force_statistical_only mode
			force_statistical_only = config_options.get(CONF_FORCE_STATISTICAL_ONLY, False)
			if is_constant_source:
				force_statistical_only = False
			
			# Decide how much energy to add this tick.
			energy_kwh = None
			method = None
			window_seconds = (now - self._last_update).total_seconds() if self._last_update else 0.0
			
			if statistical_data is not None and isinstance(statistical_data, (int, float)):
				# Statistical result covers the whole window; the accumulator
				# tracked the same period via state changes, so discard it.
				energy_kwh = statistical_data
				method = "statistical"
				self._pending_point_energy = 0.0
				self._using_statistical = True
			elif force_statistical_only:
				# Never use point sampling in this mode; the anchor was not
				# advanced, so the next successful calculation covers this period.
				_debug_log(self.hass, f"Statistical-only mode: waiting for statistical data for {self._attr_name}")
				self._pending_point_energy = 0.0
			elif self._using_statistical:
				# Statistical sensors must not mix in point sampling; the next
				# successful statistical window covers this period.
				_debug_log(self.hass, f"Previously used statistical for {self._attr_name} - not falling back to point sampling")
				self._pending_point_energy = 0.0
			elif self._last_power is not None and self._last_update is not None:
				if window_seconds > max_gap_seconds:
					_LOGGER.info(
						f"Skipping point sampling for {self._attr_name}: "
						f"{window_seconds:.0f}s gap since last update (likely restart/offline). "
						f"Resuming from fresh data to avoid phantom energy."
					)
					self._pending_point_energy = 0.0
				else:
					# Point sampling: energy accumulated from state changes since
					# the last tick, plus the final segment holding the last
					# known power up to now (left Riemann, matching statistics).
					final_segment = held_power_energy_kwh(self._last_power, window_seconds, self._power_to_kw_factor)
					energy_kwh = self._pending_point_energy + final_segment
					method = "point sampling"
					self._pending_point_energy = 0.0
					self._using_statistical = False
			else:
				_debug_log(self.hass, f"No previous data available for {self._attr_name} - will start tracking on next update")
			
			if energy_kwh is not None and energy_kwh > 0:
				# Spike protection: reject unrealistic additions (only if enabled)
				max_energy_per_hour = config_options.get(CONF_MAX_ENERGY_PER_HOUR, 0)  # 0 = disabled
				window_hours = max(window_seconds, sample_interval) / 3600
				if max_energy_per_hour > 0 and energy_kwh > max_energy_per_hour * window_hours:
					_LOGGER.warning(
						f"SPIKE DETECTED in {self._attr_name}: Attempted to add {energy_kwh:.4f} kWh "
						f"over {window_hours:.2f} hours (max allowed: {max_energy_per_hour * window_hours:.4f} kWh). "
						f"This reading has been REJECTED to prevent overreading. "
						f"Adjust 'max_energy_per_hour' in advanced settings (currently {max_energy_per_hour} kWh/h)."
					)
				else:
					self._state += energy_kwh
					self._calculation_count += 1
					unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
					if not self._first_calculation_logged:
						_info_log(self.hass, f"Energy sensor {self._attr_name} is now tracking energy from {self._source_sensor} ({unit_display} sensor) using {method}", force=True)
						self._first_calculation_logged = True
					_debug_log(self.hass, f"{method}: {self._attr_name} | Energy added: {energy_kwh:.8f}kWh | Total: {self._state:.4f}kWh | Current power: {power:.2f}{unit_display}")
			
			# Always resync tracking and persist, regardless of method used.
			self._last_power = power
			self._last_update = now
			await self._save_state()
			self.safe_write_ha_state()
				
		except Exception as e:
			_LOGGER.error(f"Unexpected error in interval update for {self._attr_name}: {e}", exc_info=True)
		finally:
			self._calculating_energy = False

	async def _handle_midnight_update(self, now):
		"""Handle midnight reset for daily energy tracking."""
		# IMPORTANT: Don't perform energy calculations here to avoid double counting
		# Energy calculations are handled exclusively by the interval timer
		# This is just for future midnight-specific tasks if needed
		_debug_log(self.hass, f"Midnight update called for {self._attr_name} - no calculation performed to avoid double counting")
		pass

	async def _handle_state_change(self, event):
		"""Update energy when power changes."""
		new_state = event.data.get("new_state")
		if new_state is None or new_state.state in ("unknown", "unavailable"):
			return

		now = dt_util.utcnow()
		power = self._state_to_power(new_state)
		if power is None:
			_LOGGER.warning(f"Invalid power value: {new_state.state}")
			return

		# If this is the first time we're getting a valid power reading, initialise tracking
		if self._last_power is None or self._last_update is None:
			_debug_log(self.hass, f"Source sensor {self._source_sensor} state change detected, initialising tracking for {self._attr_name}")
			self._last_power = power
			self._last_update = now
			await self._save_state_throttled(30)
			self.safe_write_ha_state()
			return

		# Accumulate the completed segment (previous power held until this
		# change - left Riemann). The accumulated energy is only added to the
		# total by the interval timer, and is discarded whenever a statistical
		# calculation covers the same window, so nothing is counted twice.
		delta_seconds = (now - self._last_update).total_seconds()
		if self._power_to_kw_factor and 0 < delta_seconds <= POINT_SAMPLING_MAX_GAP_SECONDS:
			self._pending_point_energy += held_power_energy_kwh(
				self._last_power, delta_seconds, self._power_to_kw_factor
			)
		
		self._last_power = power
		self._last_update = now
		# Still update the state for UI feedback
		self.safe_write_ha_state()

	async def async_will_remove_from_hass(self):
		"""Clean up resources when entity is removed."""
		try:
			if hasattr(self, "_unsub_state") and self._unsub_state:
				self._unsub_state()
		except Exception:
			pass
		try:
			if hasattr(self, "_unsub_month") and self._unsub_month:
				self._unsub_month()
		except Exception:
			pass
		# Cancel interval tracking
		if self._interval_tracker:
			self._interval_tracker()
			self._interval_tracker = None
		
		# Save state one last time
		await self._save_state()
		# Unsubscribe listeners
		try:
			if hasattr(self, "_unsub_state_change") and self._unsub_state_change:
				self._unsub_state_change()
		except Exception:
			pass
		try:
			if hasattr(self, "_unsub_midnight") and self._unsub_midnight:
				self._unsub_midnight()
		except Exception:
			pass
		try:
			if hasattr(self, "_unsub_midnight_update") and self._unsub_midnight_update:
				self._unsub_midnight_update()
		except Exception:
			pass

	@property
	def unit_of_measurement(self):
		"""Return the unit of measurement, ensuring it's always kWh."""
		return UnitOfEnergy.KILO_WATT_HOUR

	@property
	def native_unit_of_measurement(self):
		"""Return the native unit of measurement, ensuring it's always kWh."""
		return UnitOfEnergy.KILO_WATT_HOUR

	@property
	def native_value(self):
		"""Return the current state."""
		return round(self._state, 4)  # More decimal places for accuracy

	@property
	def state(self):
		"""Return the current state."""
		return round(self._state, 4)  # More decimal places for accuracy
		
	@property
	def extra_state_attributes(self):
		"""Return the state attributes."""
		attrs = {}
		if self._last_power is not None:
			attrs["last_power"] = round(self._last_power, 3)
		if self._last_update is not None:
			attrs["last_update"] = self._last_update.isoformat()
		
		# Add conversion factor for debugging
		if self._power_to_kw_factor is not None:
			attrs["power_to_kw_factor"] = self._power_to_kw_factor
			attrs["source_unit"] = "kW" if self._power_to_kw_factor == 1 else "W"
		if self._constant_power_w is not None:
			attrs["constant_power_w"] = self._constant_power_w
			attrs["constant_switch_entity"] = self._source_sensor
		
		# Add more diagnostic information
		attrs["calculation_count"] = self._calculation_count
		attrs["calculation_method"] = "statistical" if hasattr(self, '_using_statistical') and self._using_statistical else "point_sampling"
		
		# Get configuration options using the helper function
		config_options = _get_config_options(self._hass)
		attrs["statistical_calculation_enabled"] = config_options.get(CONF_USE_STATISTICAL, True)
		
		source_state = self._hass.states.get(self._source_sensor)
		if source_state:
			attrs["source_current_value"] = source_state.state
			attrs["source_unit_of_measurement"] = source_state.attributes.get("unit_of_measurement", "")
		
		# Get interval from options
		sample_interval = 60  # Default 
		# Try to get the configured sample interval from the integration's options
		for entry_id, entry_data in self._hass.data.get(DOMAIN, {}).items():
			try:
				options = entry_data.get("options") if isinstance(entry_data, dict) else None
				if isinstance(options, dict):
					sample_interval = options.get("sample_interval", 60)
					break
			except Exception:
				pass
				
		attrs["sample_interval"] = sample_interval
		return attrs

	def safe_write_ha_state(self):
		"""Safely write HA state with error handling and unit verification."""
		try:
			# Ensure unit is always set before writing state
			if not hasattr(self, '_attr_unit_of_measurement') or not self._attr_unit_of_measurement:
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
				_LOGGER.warning(f"Unit of measurement was missing for {self._attr_name}, restored to kWh")
			
			# Verify the unit is correct
			if self._attr_unit_of_measurement != UnitOfEnergy.KILO_WATT_HOUR:
				_LOGGER.warning(f"Unit of measurement was incorrect for {self._attr_name} ({self._attr_unit_of_measurement}), correcting to kWh")
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
			
			self.async_write_ha_state()
		except Exception as e:
			_LOGGER.error(f"Error writing HA state for {self._attr_name}: {e}", exc_info=True)

class SyntheticGridTotalEnergySensor(SensorEntity):
	"""Synthetic grid total energy sensor that sums all generated energy sensors."""

	def __init__(self, hass: HomeAssistant):
		self._hass = hass
		self._attr_name = "Synthetic Grid Total Energy"
		self._attr_unique_id = "synthetic_grid_total_energy"
		self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_device_class = SensorDeviceClass.ENERGY
		self._attr_state_class = SensorStateClass.TOTAL_INCREASING
		self._attr_entity_registry_enabled_default = True
		self._attr_device_info = DeviceInfo(
			identifiers={(DOMAIN, "synthetic_grid_total")},
			name="Synthetic Grid",
			manufacturer="Energy Sensor Generator",
			model="Synthetic Grid Total"
		)
		self._state = 0.0
		self._sources = set()
		self._unsub_listeners = []
		self._rescan_interval = None

	def _find_sources(self) -> set:
		"""Find main energy sensors created by this integration to include in the total."""
		entity_registry = er.async_get(self._hass)
		sources = set()
		for entity_id, entry in entity_registry.entities.items():
			if entry.platform != DOMAIN:
				continue
			# Only include main energy sensors (exclude period variants)
			uid = entry.unique_id or ""
			if uid == self._attr_unique_id:
				continue
			if uid.endswith("_energy") and not any(x in uid for x in ["_daily_energy", "_monthly_energy", "_weekly_energy", "_annual_energy"]):
				sources.add(entity_id)
		return sources

	async def async_added_to_hass(self):
		await self._setup_sources_and_listeners()
		# Periodically rescan to pick up newly added sensors
		self._rescan_interval = async_track_time_interval(
			self._hass,
			self._handle_rescan,
			timedelta(seconds=60)
		)
		self.safe_write_ha_state()

	async def async_will_remove_from_hass(self):
		for unsub in self._unsub_listeners:
			try:
				unsub()
			except Exception:
				pass
		self._unsub_listeners = []
		if self._rescan_interval:
			try:
				self._rescan_interval()
			except Exception:
				pass
			self._rescan_interval = None

	async def _setup_sources_and_listeners(self):
		new_sources = self._find_sources()
		if new_sources != self._sources:
			# Update listeners
			for unsub in self._unsub_listeners:
				try:
					unsub()
				except Exception:
					pass
			self._unsub_listeners = []
			self._sources = new_sources
			if self._sources:
				self._unsub_listeners.append(
					async_track_state_change_event(self._hass, list(self._sources), self._handle_source_change)
				)
		await self._recalculate_total()

	async def _handle_rescan(self, now):
		await self._setup_sources_and_listeners()

	async def _handle_source_change(self, event):
		await self._recalculate_total()

	async def _recalculate_total(self):
		total = 0.0
		for entity_id in self._sources:
			state = self._hass.states.get(entity_id)
			if not state or state.state in ("unknown", "unavailable"):
				continue
			try:
				value = float(state.state)
			except (ValueError, TypeError):
				continue
			if value >= 0:
				total += value
		self._state = total
		self.safe_write_ha_state()

	@property
	def native_value(self):
		return round(self._state, 4)

	@property
	def state(self):
		return round(self._state, 4)

	@property
	def extra_state_attributes(self):
		return {
			"sources": sorted(list(self._sources))
		}

	def safe_write_ha_state(self):
		try:
			if not hasattr(self, '_attr_unit_of_measurement') or not self._attr_unit_of_measurement:
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
			if self._attr_unit_of_measurement != UnitOfEnergy.KILO_WATT_HOUR:
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
			self.async_write_ha_state()
		except Exception as e:
			_LOGGER.error(f"Error writing HA state for {self._attr_name}: {e}", exc_info=True)
