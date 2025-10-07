import logging
from datetime import datetime, timedelta
import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    SensorEntity, 
    SensorDeviceClass,
    SensorStateClass
)
from homeassistant.const import UnitOfEnergy
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
    from homeassistant.components.recorder import statistics
    STATISTICS_AVAILABLE = True
except ImportError:
    STATISTICS_AVAILABLE = False
    _LOGGER.warning("Statistics module not available, using point sampling only")
from .utils import StorageManager
from .const import (
	DOMAIN, 
	CONF_DEBUG_LOGGING, 
	CONF_USE_STATISTICAL,
	CONF_FORCE_STATISTICAL_ONLY,
	CONF_STAT_LOOKBACK_MINUTES,
	CONF_MAX_ENERGY_PER_HOUR
)
import time

def _is_debug_enabled(hass: HomeAssistant) -> bool:
	"""Check if debug logging is enabled for this integration."""
	if DOMAIN not in hass.data:
		return False
	
	# Check all config entries for debug setting
	for config_entry in hass.config_entries.async_entries(DOMAIN):
		if config_entry.options.get(CONF_DEBUG_LOGGING, False):
			return True
	return False

def _debug_log(hass: HomeAssistant, message: str) -> None:
	"""Log debug message only if debug logging is enabled and not too frequently."""
	if _is_debug_enabled(hass):
		# Add throttling to prevent log spam
		current_time = time.time()
		
		# Create a simple throttling mechanism using the domain data
		if DOMAIN not in hass.data:
			return
			
		# Store last log times per message type to throttle similar messages
		log_throttle_key = "_debug_log_throttle"
		if log_throttle_key not in hass.data[DOMAIN]:
			hass.data[DOMAIN][log_throttle_key] = {}
		
		# Create a simple hash of the message to group similar messages
		import hashlib
		message_hash = hashlib.md5(message[:50].encode()).hexdigest()[:8]  # Use first 50 chars for grouping
		
		last_log_time = hass.data[DOMAIN][log_throttle_key].get(message_hash, 0)
		
		# Only log if it's been at least 30 seconds since the last similar message
		if current_time - last_log_time > 30:
			_LOGGER.warning(f"DEBUG: {message}")
			hass.data[DOMAIN][log_throttle_key][message_hash] = current_time

def _info_log(hass: HomeAssistant, message: str, force: bool = False) -> None:
	"""Log info message, respecting debug setting unless forced."""
	if force or _is_debug_enabled(hass):
		_LOGGER.info(message)

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

def get_friendly_name(hass: HomeAssistant, entity_id: str) -> str:
	"""Get the friendly name for an entity, falling back to derived name from entity ID."""
	entity_registry = er.async_get(hass)
	entity_entry = entity_registry.async_get(entity_id)
	
	# Try to get friendly_name from entity state first (this is what users see in the UI)
	state = hass.states.get(entity_id)
	if state and state.attributes.get("friendly_name"):
		name = state.attributes["friendly_name"]
		if name.lower().endswith(" power"):
			name = name[:-6]  # Remove " power"
		elif name.lower().endswith("_power"):
			name = name[:-6]  # Remove "_power"
		return name
	
	# Try to get custom name from entity registry
	if entity_entry and entity_entry.name:
		# Remove "_power" suffix if present to get clean base name
		name = entity_entry.name
		if name.lower().endswith(" power"):
			name = name[:-6]  # Remove " power"
		elif name.lower().endswith("_power"):
			name = name[:-6]  # Remove "_power"
		return name
	
	# Try to get device name if entity is part of a device
	if entity_entry and entity_entry.device_id:
		device_registry = dr.async_get(hass)
		device = device_registry.async_get(entity_entry.device_id)
		if device and device.name_by_user:
			return device.name_by_user
		elif device and device.name:
			return device.name
	
	# Fall back to deriving name from entity ID
	# Convert entity_id like "sensor.smart_plug_2_power" to "Smart Plug 2"
	base_name = entity_id.replace("sensor.", "").replace("_power", "")
	# Convert underscores to spaces and title case
	return base_name.replace("_", " ").title()

def get_friendly_name_from_base(hass: HomeAssistant, base_name: str) -> str:
	"""Get friendly name by trying different possible power sensor patterns."""
	# Handle disambiguated base names (e.g., "smart_plug_energy_2" from "sensor.smart_plug_power_2")
	# Need to reverse the transformation to find the original power sensor
	possible_sensors = []
	
	if "_energy_" in base_name:
		# Disambiguated pattern: "smart_plug_energy_2" -> "sensor.smart_plug_power_2"
		parts = base_name.split("_energy_")
		if len(parts) == 2:
			original_power_sensor = f"sensor.{parts[0]}_power_{parts[1]}"
			possible_sensors.append(original_power_sensor)
	
	# Standard patterns
	possible_sensors.extend([
		f"sensor.{base_name}_power",
		f"sensor.{base_name}",
		f"{base_name}_power",
		f"{base_name}"
	])
	
	for sensor_id in possible_sensors:
		if hass.states.get(sensor_id):
			return get_friendly_name(hass, sensor_id)
	
	# If no sensor found, just clean up the base_name
	return base_name.replace("_", " ").replace("_energy_", " ").title()

def get_unique_entity_name(hass: HomeAssistant, proposed_name: str, domain: str = "sensor") -> str:
	"""Generate a unique entity name by checking for conflicts and adding suffixes if needed."""
	entity_registry = er.async_get(hass)
	
	# Check if the proposed name conflicts with any existing entity
	base_name = proposed_name
	counter = 1
	
	while True:
		# Check if any entity has this name
		name_exists = False
		conflicting_entity = None
		is_own_entity = False
		
		for entity_id, entry in entity_registry.entities.items():
			if entity_id.startswith(f"{domain}."):
				_curr_name = (entry.name or entry.original_name or "").lower()
				if _curr_name == proposed_name.lower():
					# Check if this conflicting entity is from our own integration
					if entry.platform == DOMAIN:
						# It's our own entity, don't treat as conflict
						is_own_entity = True
						_LOGGER.debug(f"Detected own entity with name '{proposed_name}': {entity_id}")
					else:
						name_exists = True
						conflicting_entity = entity_id
					break
		
		# Also check current states for entities that might not be in registry yet
		# But skip this check if we already found it's our own entity
		if not name_exists and not is_own_entity:
			for state in hass.states.async_all():
				if (state.entity_id.startswith(f"{domain}.") and 
					state.attributes.get("friendly_name", "").lower() == proposed_name.lower()):
					name_exists = True
					conflicting_entity = state.entity_id
					break
		
		if not name_exists:
			if counter > 1:
				_LOGGER.info(f"Entity name conflict resolved: using '{proposed_name}' instead of '{base_name}'")
			return proposed_name
		
		# Name exists, try with a suffix
		if counter == 2:  # Log only on first conflict detection
			_LOGGER.warning(f"Entity name conflict detected: '{base_name}' already exists (conflicting entity: {conflicting_entity}). Adding suffix.")
		
		counter += 1
		proposed_name = f"{base_name} ({counter})"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
	"""Set up the sensor platform."""
	# Store the async_add_entities callback for later use by generate_sensors_service
	if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
		hass.data[DOMAIN][entry.entry_id]["async_add_entities"] = async_add_entities
	
	# Check if we need to recreate existing entities during reload
	options = entry.options
	
	# Only proceed if we have selected sensors configured
	selected_sensors = options.get("selected_power_sensors", [])
	_LOGGER.info(f"Setting up energy sensors for selected power sensors: {selected_sensors}")
	if not selected_sensors:
		_LOGGER.warning("No power sensors selected in configuration, skipping sensor setup")
		return
	
	# Find existing generated sensors
	entity_registry = er.async_get(hass)
	existing_entities = []
	
	# Look for entities with this integration's platform
	for entity_id, entity_entry in entity_registry.entities.items():
		if entity_entry.platform == DOMAIN and entity_entry.config_entry_id == entry.entry_id:
			existing_entities.append((entity_id, entity_entry.unique_id))
	
	# If we have existing entities, recreate them to ensure they're properly linked
	# But only if we have selected sensors configured
	if existing_entities and selected_sensors:
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
			expected_source_sensor = f"sensor.{base_name}_power"
			source_sensor = expected_source_sensor
			
			# Debug the mapping process
			_LOGGER.debug(f"Recreating sensors for base_name '{base_name}', expected source: '{expected_source_sensor}'")
			_LOGGER.debug(f"Selected sensors: {selected_sensors}")
			
			# Verify the expected source sensor is in the selected list
			if expected_source_sensor not in selected_sensors:
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
				# Validate that this is actually a power sensor
				source_state = hass.states.get(source_sensor)
				unit = source_state.attributes.get("unit_of_measurement", "")
				device_class = source_state.attributes.get("device_class", "")
				if unit in ["kWh", "kwh"] or device_class == "energy":
					_LOGGER.error(f"CRITICAL ERROR during startup: Source sensor {source_sensor} for {base_name} is an ENERGY sensor (unit: {unit}, device_class: {device_class}) instead of a POWER sensor. Skipping recreation.")
					continue
				else:
					_LOGGER.debug(f"Validated source sensor {source_sensor} is a power sensor (unit: {unit}, device_class: {device_class})")
			
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
				
				energy_sensor = EnergySensor(hass, base_name, source_sensor, storage_manager, device_identifiers)
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
	
	return

class EnergySensor(SensorEntity, RestoreEntity):
    """Custom sensor to calculate kWh from power (Watts)."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        # Backwards compat: storage_path param may be Path; but prefer StorageManager
        self._storage_manager: StorageManager | None = storage_path if isinstance(storage_path, StorageManager) else None
        self._storage_path = storage_path  # kept for type compatibility
        self._device_identifiers = device_identifiers
        
        # Validate that we're not creating an energy sensor from an energy source
        source_state = hass.states.get(source_sensor)
        if source_state:
            unit = source_state.attributes.get("unit_of_measurement", "")
            device_class = source_state.attributes.get("device_class", "")
            if unit in ["kWh", "kwh"] or device_class == "energy":
                _LOGGER.error(f"CONFIGURATION ERROR: Cannot create energy sensor from energy source '{source_sensor}' (unit: {unit}, device_class: {device_class}). Energy sensors must be created from POWER sensors with unit 'W' or 'kW'. Please reconfigure this integration to monitor power sensors instead.")
                # Don't raise an exception to avoid breaking startup, but log the error clearly
        
        # Get friendly name from the source sensor
        friendly_name = get_friendly_name(hass, source_sensor)
        
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
        self._power_to_kw_factor = None
        
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
        # State will be loaded in async_added_to_hass

    def _get_power_conversion_factor(self, hass, source_sensor):
        """Determine the conversion factor from source power unit to kW."""
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
                
            # Ensure we have a reasonable time range (at least 30 seconds for reliable calculation)
            time_delta = (end_time - start_time).total_seconds()
            if time_delta < 30:  # Reduced from 60 to 30 seconds for better frequent sensor support
                _debug_log(self.hass, f"Time range too short for statistical calculation ({time_delta:.1f}s) for {self._attr_name} - need at least 30 seconds")
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
                    
                    if len(states_list) < 2:
                        return {"error": f"Not enough historical states ({len(states_list)}) - need at least 2 data points for statistical calculation"}
                    
                    # Filter out invalid states and convert to numeric values
                    valid_states = []
                    for state in states_list:
                        try:
                            if state.state not in ("unknown", "unavailable", None):
                                power_value = float(state.state)
                                if power_value >= 0:  # Only accept non-negative power values
                                    valid_states.append({
                                        'power': power_value,
                                        'time': state.last_updated
                                    })
                        except (ValueError, TypeError, AttributeError):
                            continue
                    
                    if len(valid_states) < 2:
                        return {"error": f"Not enough valid states ({len(valid_states)}) from {len(states_list)} total - need at least 2 valid data points"}
                    
                    # Sort by time to ensure chronological order
                    valid_states.sort(key=lambda x: x['time'])
                    
                    # Calculate energy using LEFT Riemann sum (like Home Assistant's integration sensor)
                    # This method assumes constant power between readings, which is correct for IoT devices
                    total_energy = 0.0
                    calculation_count = 0
                    segments = []
                    max_power = 0.0
                    min_power = float('inf')
                    total_power = 0.0
                    
                    for i in range(1, len(valid_states)):
                        prev_state = valid_states[i-1]
                        curr_state = valid_states[i]
                        
                        # Track power statistics
                        max_power = max(max_power, prev_state['power'], curr_state['power'])
                        min_power = min(min_power, prev_state['power'], curr_state['power'])
                        total_power += prev_state['power']
                        
                        # Calculate time difference in hours
                        time_delta_hours = (curr_state['time'] - prev_state['time']).total_seconds() / 3600.0
                        
                        # Skip unreasonably large gaps (> 6 hours) as they're likely data gaps
                        if time_delta_hours > 0 and time_delta_hours < 6:
                            # LEFT Riemann sum: use the previous power value for the entire interval
                            # This correctly handles devices that are either ON at a fixed power or OFF (0W)
                            power = prev_state['power']
                            
                            # Convert to kWh using the conversion factor
                            energy_increment = (power * time_delta_hours) / conversion_factor
                            total_energy += energy_increment
                            calculation_count += 1
                            
                            segments.append({
                                'power': power,
                                'duration_seconds': time_delta_hours * 3600,
                                'energy_kwh': energy_increment
                            })
                       
                    avg_power = (total_power / calculation_count) if calculation_count > 0 else 0.0
                    
                    return {
                        "total_energy": total_energy if total_energy > 0 and calculation_count > 0 else None,
                        "segments": calculation_count,
                        "total_states": len(states_list),
                        "valid_states": len(valid_states),
                        "max_power": max_power,
                        "min_power": min_power if min_power != float('inf') else 0.0,
                        "avg_power": avg_power,
                        "segment_details": segments[:3]  # Only return first 3 segments for logging
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
            
            if statistical_energy is not None and statistical_energy > 0:
                _debug_log(self.hass, f"Statistical calculation successful for {self._attr_name}: {statistical_energy:.8f}kWh over {time_delta:.1f}s")
                _debug_log(self.hass, f"  Found {statistical_data['total_states']} states, {statistical_data['valid_states']} valid, {calculation_count} segments")
                _debug_log(self.hass, f"  Power range: {statistical_data.get('min_power', 0):.2f}W to {max_power:.2f}W, avg: {avg_power:.2f}W")
                if statistical_data.get('segment_details'):
                    for i, seg in enumerate(statistical_data['segment_details']):
                        _debug_log(self.hass, f"  Segment {i+1}: {seg['power']:.2f}W over {seg['duration_seconds']:.1f}s = {seg['energy_kwh']:.8f}kWh")
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
        if self._storage_manager:
            storage = await self._storage_manager.async_load()
        else:
            from homeassistant.helpers import storage as ha_storage
            store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
            storage = await store.async_load() or {}
        storage[self._storage_key] = {
            "value": self._state,
            "last_power": self._last_power,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "last_statistical_calculation": self._last_statistical_calculation.isoformat() if self._last_statistical_calculation else None,
            "conversion_factor": self._power_to_kw_factor
        }
        try:
            if self._storage_manager:
                await self._storage_manager.async_save(storage)
            else:
                from homeassistant.helpers import storage as ha_storage
                store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
                await store.async_save(storage)
        except Exception:
            pass

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
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    power = float(state.state)
                except (ValueError, TypeError):
                    _LOGGER.warning(f"Unable to initialise {self._attr_name} - invalid power state (cannot convert to float): {state.state}")
                else:
                    # Successfully converted to float, now try to save state
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
                _debug_log(self.hass, f"Source sensor {self._source_sensor} not yet available during startup for {self._base_name}")
        
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
            
            _debug_log(self.hass, f"Configuration: statistical={use_statistical}, stats_available={STATISTICS_AVAILABLE}")
            
            # Try statistical calculation first if enabled
            statistical_data = None
            if use_statistical and STATISTICS_AVAILABLE:
                try:
                    config_options = _get_config_options(self.hass)
                    lookback_minutes = max(10, int(config_options.get(CONF_STAT_LOOKBACK_MINUTES, 30)))

                    # Determine the correct time window for statistical calculation
                    if hasattr(self, '_last_statistical_calculation') and self._last_statistical_calculation:
                        # Incremental calculation: only calculate since last successful calculation
                        # NO buffer to prevent double-counting! Start exactly where we left off.
                        stat_start_time = self._last_statistical_calculation
                        stat_end_time = now
                        window_description = f"incremental since {self._last_statistical_calculation}"
                        _debug_log(self.hass, f"Using incremental statistical calculation: {stat_start_time} to {stat_end_time}")

                        # Safety check: if the time since last calculation is very short (<10 seconds),
                        # it might indicate rapid successive calls - skip to avoid double counting
                        time_since_last = (now - self._last_statistical_calculation).total_seconds()
                        if time_since_last < 10:
                            _debug_log(self.hass, f"Skipping statistical calculation - too soon since last calculation ({time_since_last:.1f}s ago)")
                            statistical_data = 0  # Treat as successful but no new energy
                        else:
                            statistical_data = await self._get_statistical_power_data(stat_start_time, stat_end_time)
                    elif hasattr(self, '_last_update') and self._last_update:
                        # We have a last_update time but no last_statistical_calculation
                        # This happens after restart - use last_update to avoid recounting old data
                        stat_start_time = self._last_update
                        stat_end_time = now
                        window_description = f"from last_update {self._last_update}"
                        _debug_log(self.hass, f"Post-restart calculation using last_update: {stat_start_time} to {stat_end_time}")
                        _LOGGER.warning(f"Using last_update for {self._attr_name} to prevent double-counting on restart (last update: {self._last_update})")
                        statistical_data = await self._get_statistical_power_data(stat_start_time, stat_end_time)
                    else:
                        # True first calculation: no previous tracking at all
                        # Use lookback window but LOG A WARNING since this adds historical data
                        stat_start_time = now - timedelta(minutes=lookback_minutes)
                        stat_end_time = now
                        window_description = f"{lookback_minutes}min lookback (FIRST CALCULATION)"
                        _LOGGER.warning(f"FIRST CALCULATION for {self._attr_name} using {lookback_minutes}min lookback - this will add historical energy!")
                        _debug_log(self.hass, f"Initial statistical calculation using {lookback_minutes}min lookback: {stat_start_time} to {stat_end_time}")
                        statistical_data = await self._get_statistical_power_data(stat_start_time, stat_end_time)

                    # If calculation failed, try fallback strategies
                    if statistical_data is None:
                        # Strategy 1: If we have last_statistical_calculation, try with tiny buffer
                        if (hasattr(self, '_last_statistical_calculation') and
                            self._last_statistical_calculation and
                            (now - self._last_statistical_calculation).total_seconds() < 300):  # Only if last calc was recent (<5min)

                            # Try starting 30 seconds before last calculation (minimal overlap to catch late-arriving data)
                            extended_start = self._last_statistical_calculation - timedelta(seconds=30)
                            stat_end_time = now
                            _debug_log(self.hass, f"Retrying with minimal buffer from last_statistical: {extended_start} to {stat_end_time}")

                            # Safety check: ensure we're not overlapping too much
                            time_since_last = (now - self._last_statistical_calculation).total_seconds()
                            if time_since_last < 10:
                                _debug_log(self.hass, f"Skipping retry - too soon since last calculation ({time_since_last:.1f}s ago)")
                                statistical_data = 0
                            else:
                                statistical_data = await self._get_statistical_power_data(extended_start, stat_end_time)
                        
                        # Strategy 2: If we have last_update but no statistical calc (post-restart), try with tiny buffer
                        elif (hasattr(self, '_last_update') and
                              self._last_update and
                              (now - self._last_update).total_seconds() < 300):
                            
                            extended_start = self._last_update - timedelta(seconds=30)
                            stat_end_time = now
                            _debug_log(self.hass, f"Retrying with minimal buffer from last_update: {extended_start} to {stat_end_time}")
                            statistical_data = await self._get_statistical_power_data(extended_start, stat_end_time)

                    # If successful, update the last statistical calculation time
                    if statistical_data is not None and isinstance(statistical_data, (int, float)) and statistical_data > 0:
                        self._last_statistical_calculation = now
                        _debug_log(self.hass, f"Statistical calculation successful for {self._attr_name}: {statistical_data:.8f}kWh ({window_description})")
                    else:
                        _debug_log(self.hass, f"Statistical calculation failed for {self._attr_name} - will fall back to point sampling")
                except Exception as e:
                    _debug_log(self.hass, f"Exception during statistical calculation: {str(e)}")
                    statistical_data = None
                
            state = self._hass.states.get(self._source_sensor)
            if not state:
                _debug_log(self.hass, f"Source sensor {self._source_sensor} not found for {self._attr_name}")
                return
                
            if state.state in ("unknown", "unavailable"):
                _debug_log(self.hass, f"Source sensor {self._source_sensor} has invalid state '{state.state}' for {self._attr_name}")
                return
                
            try:
                power = float(state.state)
            except (ValueError, TypeError):
                _debug_log(self.hass, f"Invalid power value '{state.state}' from {self._source_sensor} for {self._attr_name}")
                return
            
            # Log when we're actually starting calculations
            _debug_log(self.hass, f"Interval update called for {self._attr_name} - power: {power}W, source: {self._source_sensor}")
            
            # Add diagnostic information about the source sensor
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
            
            # Use statistical data if available, otherwise optionally fall back to point sampling
            if statistical_data is not None and isinstance(statistical_data, (int, float)) and statistical_data > 0:
                # Spike protection: Check if the energy added is unrealistic (only if enabled)
                max_energy_per_hour = config_options.get(CONF_MAX_ENERGY_PER_HOUR, 0)  # 0 = disabled
                
                # Calculate expected max for this time interval (only if spike protection is enabled)
                if max_energy_per_hour > 0 and self._last_update:
                    time_delta_hours = (now - self._last_update).total_seconds() / 3600
                    max_allowed = max_energy_per_hour * time_delta_hours
                    
                    if statistical_data > max_allowed:
                        _LOGGER.warning(
                            f"SPIKE DETECTED in {self._attr_name}: Attempted to add {statistical_data:.4f} kWh "
                            f"over {time_delta_hours:.2f} hours (max allowed: {max_allowed:.4f} kWh). "
                            f"This reading has been REJECTED to prevent overreading. "
                            f"Adjust 'max_energy_per_hour' in advanced settings (currently {max_energy_per_hour} kWh/h)."
                        )
                        # Skip this reading but update tracking variables
                        self._last_power = power
                        self._last_update = now
                        await self._save_state()
                        self.safe_write_ha_state()
                        return
                
                old_state = self._state
                self._state += statistical_data
                _debug_log(self.hass, f"PRECISE DEBUG: {self._attr_name} | Before: {old_state:.10f}kWh | Adding: {statistical_data:.10f}kWh | After: {self._state:.10f}kWh")
                self._using_statistical = True
                unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                self._calculation_count += 1
                if not self._first_calculation_logged:
                    _info_log(self.hass, f"Energy sensor {self._attr_name} is now tracking energy from {self._source_sensor} ({unit_display} sensor) using statistical data", force=True)
                    self._first_calculation_logged = True
                _debug_log(self.hass, f"Statistical energy calculation: {self._attr_name} | Energy added: {statistical_data:.8f}kWh | Total: {self._state:.4f}kWh | Current power: {power:.2f}{unit_display}")
            else:
                # Statistical calculation failed or returned no data
                # Check if we should fall back to point sampling or skip
                
                if force_statistical_only:
                    # In force_statistical_only mode: NEVER use point sampling
                    _debug_log(self.hass, f"Statistical-only mode: No statistical data available for {self._attr_name}, skipping calculation (will retry next interval)")
                    # Update tracking but don't add any energy
                    self._last_power = power
                    self._last_update = now
                    await self._save_state()
                    self.safe_write_ha_state()
                    return
                    
                # Point sampling fallback (only if force_statistical_only is False)
                # Also check if we've EVER used statistical - if so, don't go back to point sampling
                if (not force_statistical_only and 
                    self._last_power is not None and 
                    self._last_update is not None and
                    not self._using_statistical):  # Never switch back from statistical to point sampling
                    time_delta = (now - self._last_update).total_seconds()
                    delta_hours = time_delta / 3600
                    _debug_log(self.hass, f"Point sampling calculation for {self._attr_name} | Last power: {self._last_power} | Current power: {power} | Time delta: {time_delta:.0f}s")
                    avg_power = (self._last_power + power) / 2
                    energy_kwh = (avg_power * delta_hours) / self._power_to_kw_factor
                    _debug_log(self.hass, f"Calculated energy: {energy_kwh:.8f}kWh | Avg power: {avg_power:.4f} | Delta hours: {delta_hours:.6f} | Conversion factor: {self._power_to_kw_factor}")
                    if energy_kwh > 0:
                        # Spike protection for point sampling too (only if enabled)
                        max_energy_per_hour = config_options.get(CONF_MAX_ENERGY_PER_HOUR, 0)  # 0 = disabled
                        
                        if max_energy_per_hour > 0:
                            max_allowed = max_energy_per_hour * delta_hours
                            
                            if energy_kwh > max_allowed:
                                _LOGGER.warning(
                                    f"SPIKE DETECTED in {self._attr_name}: Attempted to add {energy_kwh:.4f} kWh "
                                    f"over {delta_hours:.2f} hours (max allowed: {max_allowed:.4f} kWh). "
                                    f"This reading has been REJECTED to prevent overreading. "
                                    f"Adjust 'max_energy_per_hour' in advanced settings (currently {max_energy_per_hour} kWh/h)."
                                )
                                # Skip this reading but update tracking variables
                                self._last_power = power
                                self._last_update = now
                                await self._save_state()
                                self.safe_write_ha_state()
                                return
                        
                        self._state += energy_kwh
                        self._using_statistical = False
                        unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                        self._calculation_count += 1
                        if not self._first_calculation_logged:
                            _info_log(self.hass, f"Energy sensor {self._attr_name} is now tracking energy from {self._source_sensor} ({unit_display} sensor) using point sampling", force=True)
                            self._first_calculation_logged = True
                        _debug_log(self.hass, f"Point sampling: {self._attr_name} | Energy added: {energy_kwh:.8f}kWh | Total: {self._state:.4f}kWh")
                    else:
                        unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                        _debug_log(self.hass, f"No energy added (too small): avg power: {avg_power:.4f}{unit_display}, calculated energy: {energy_kwh:.8f}kWh")
                elif force_statistical_only:
                    # In force_statistical_only mode, we're waiting for statistical data
                    _debug_log(self.hass, f"Statistical-only mode: Waiting for statistical data for {self._attr_name} (will not use point sampling)")
                elif self._using_statistical:
                    # We've used statistical before - don't fall back to point sampling!
                    _debug_log(self.hass, f"Previously used statistical for {self._attr_name} - not falling back to point sampling")
                else:
                    _debug_log(self.hass, f"Interval update: Point sampling enabled but no previous data available for {self._attr_name} - will start tracking on next update")

                # Update values (always update regardless of calculation method used)
                self._last_power = power
                self._last_update = now

                # Log when we first start tracking
                if self._calculation_count == 0:
                    _debug_log(self.hass, f"Starting to track power for {self._attr_name}: {power}W from {self._source_sensor}")

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
        try:
            power = float(new_state.state)
        except ValueError:
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

        # Only update tracking variables, do not perform energy calculations here
        # Energy calculations are handled exclusively by the interval timer to prevent double counting
        _LOGGER.debug(f"State change detected: {power}W - tracking only, calculation handled by interval timer")
        
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

class DailyEnergySensor(SensorEntity, RestoreEntity):
    """Custom sensor for daily energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        # Prefer StorageManager if provided
        self._storage_manager: StorageManager | None = storage_path if isinstance(storage_path, StorageManager) else None
        self._storage_path = storage_path
        
        # Get friendly name - for daily/monthly sensors, derive from base_name
        # since the source_sensor is the energy sensor, not the original power sensor
        # Try different possible patterns to find the original power sensor
        friendly_name = get_friendly_name_from_base(hass, base_name)
        
        # Generate unique entity name to avoid conflicts
        proposed_name = f"{friendly_name} Daily Energy"
        unique_name = get_unique_entity_name(hass, proposed_name)
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_daily_energy"
        self._attr_name = unique_name
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_entity_registry_enabled_default = True
        
        # Set device info directly if provided, otherwise get from source sensor
        if device_identifiers:
            self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
        else:
            # Get source sensor information to link to its device if possible
            entity_registry = er.async_get(hass)
            source_entity = entity_registry.async_get(source_sensor)
            
            # Set device info to match the source sensor's device
            if source_entity and source_entity.device_id:
                device_registry = dr.async_get(hass)
                device = device_registry.async_get(source_entity.device_id)
                if device:
                    # Use the exact same device_info as the source sensor
                    self._attr_device_info = DeviceInfo(identifiers=device.identifiers)
        
        self._state = 0.0
        self._last_energy = 0.0
        self._last_reset = None
        self._storage_key = f"{base_name}_daily_energy"
        # State will be loaded in async_added_to_hass

    async def _load_state(self):
        """Load state from storage."""
        if self._storage_manager:
            storage = await self._storage_manager.async_load()
        else:
            from homeassistant.helpers import storage as ha_storage
            store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
            storage = await store.async_load() or {}
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
        self._last_energy = state_data.get("last_energy", 0.0)

    async def _save_state(self):
        """Save state to storage."""
        if self._storage_manager:
            storage = await self._storage_manager.async_load()
        else:
            from homeassistant.helpers import storage as ha_storage
            store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
            storage = await store.async_load() or {}
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset,
            "last_energy": self._last_energy
        }
        if self._storage_manager:
            await self._storage_manager.async_save(storage)
        else:
            from homeassistant.helpers import storage as ha_storage
            store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
            await store.async_save(storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Load state from storage first
        await self._load_state()
        # Restore HA cache if storage empty
        if self._state == 0.0:
            last = await self.async_get_last_state()
            try:
                if last and last.state not in ("unknown", "unavailable", None):
                    self._state = float(last.state)
            except (ValueError, TypeError):
                pass
        
        # Track state changes to the power sensor
        self._unsub_state = async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Set up midnight reset
        self._unsub_midnight = async_track_time_change(
            self._hass,
            self._handle_midnight_reset,
            hour=0,
            minute=0,
            second=0
        )
        self.safe_write_ha_state()

    async def _handle_midnight_reset(self, now):
        """Reset at midnight."""
        _LOGGER.info(f"Midnight reset for {self._attr_name}")
        self._state = 0.0
        self._last_reset = now.isoformat()
        # Get current energy value to track from zero
        state = self._hass.states.get(self._source_sensor)
        if state and state.state not in ("unknown", "unavailable"):
            try:
                self._last_energy = float(state.state)
            except (ValueError, TypeError):
                self._last_energy = 0.0
        else:
            self._last_energy = 0.0
            
        await self._save_state()
        self.safe_write_ha_state()

    async def async_will_remove_from_hass(self):
        """Clean up resources when entity is removed."""
        try:
            if hasattr(self, "_unsub_state") and self._unsub_state:
                self._unsub_state()
        except Exception:
            pass
        try:
            if hasattr(self, "_unsub_midnight") and self._unsub_midnight:
                self._unsub_midnight()
        except Exception:
            pass

    async def _handle_state_change(self, event):
        """Update daily energy when source changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        try:
            energy = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid energy value: {new_state.state}")
            return

        # If this is the first time we're getting a valid energy reading, initialise tracking
        if self._last_energy == 0.0:
            _debug_log(self.hass, f"Source energy sensor {self._source_sensor} became available, initialising daily tracking for {self._attr_name}")
            self._last_energy = energy
            await self._save_state()
            self.safe_write_ha_state()
            return

        # Calculate the energy change
        energy_change = max(0, energy - self._last_energy)
        self._state += energy_change
        self._last_energy = energy
        
        await self._save_state()
        self.safe_write_ha_state()

    @property
    def native_value(self):
        """Return the current state."""
        return round(self._state, 4)  # Match main energy sensor precision

    @property
    def state(self):
        """Return the current state."""
        return round(self._state, 4)  # Match main energy sensor precision

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement, ensuring it's always kWh."""
        return UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_unit_of_measurement(self):
        """Return the native unit of measurement, ensuring it's always kWh."""
        return UnitOfEnergy.KILO_WATT_HOUR

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "last_reset": self._last_reset
        }

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

class MonthlyEnergySensor(SensorEntity, RestoreEntity):
    """Custom sensor for monthly energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_manager: StorageManager | None = storage_path if isinstance(storage_path, StorageManager) else None
        self._storage_path = storage_path
        
        # Get friendly name - for daily/monthly sensors, derive from base_name
        # since the source_sensor is the energy sensor, not the original power sensor
        # Try different possible patterns to find the original power sensor
        friendly_name = get_friendly_name_from_base(hass, base_name)
        
        # Generate unique entity name to avoid conflicts
        proposed_name = f"{friendly_name} Monthly Energy"
        unique_name = get_unique_entity_name(hass, proposed_name)
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_monthly_energy"
        self._attr_name = unique_name
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_entity_registry_enabled_default = True
        
        # Set device info directly if provided, otherwise get from source sensor
        if device_identifiers:
            self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
        else:
            # Get source sensor information to link to its device if possible
            entity_registry = er.async_get(hass)
            source_entity = entity_registry.async_get(source_sensor)
            
            # Set device info to match the source sensor's device
            if source_entity and source_entity.device_id:
                device_registry = dr.async_get(hass)
                device = device_registry.async_get(source_entity.device_id)
                if device:
                    # Use the exact same device_info as the source sensor
                    self._attr_device_info = DeviceInfo(identifiers=device.identifiers)
        
        self._state = 0.0
        self._last_energy = 0.0
        self._last_reset = None
        self._storage_key = f"{base_name}_monthly_energy"
        # State will be loaded in async_added_to_hass

    async def _load_state(self):
        """Load state from storage."""
        if self._storage_manager:
            storage = await self._storage_manager.async_load()
        else:
            from homeassistant.helpers import storage as ha_storage
            store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
            storage = await store.async_load() or {}
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
        self._last_energy = state_data.get("last_energy", 0.0)

    async def _save_state(self):
        """Save state to storage."""
        if self._storage_manager:
            storage = await self._storage_manager.async_load()
        else:
            from homeassistant.helpers import storage as ha_storage
            store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
            storage = await store.async_load() or {}
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset,
            "last_energy": self._last_energy
        }
        if self._storage_manager:
            await self._storage_manager.async_save(storage)
        else:
            from homeassistant.helpers import storage as ha_storage
            store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
            await store.async_save(storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Load state from storage first
        await self._load_state()
        if self._state == 0.0:
            last = await self.async_get_last_state()
            try:
                if last and last.state not in ("unknown", "unavailable", None):
                    self._state = float(last.state)
            except (ValueError, TypeError):
                pass
        
        # Track state changes to the power sensor
        self._unsub_state = async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Set up first-of-month reset (check at midnight each day)
        self._unsub_month = async_track_time_change(
            self._hass,
            self._handle_month_reset,
            hour=0,
            minute=0,
            second=0
        )
        self.safe_write_ha_state()

    async def _handle_month_reset(self, now):
        """Reset at first day of month."""
        # Check if it's the first day of the month
        if now.day == 1:
            _LOGGER.info(f"Monthly reset for {self._attr_name}")
            self._state = 0.0
            self._last_reset = now.isoformat()
            
            # Get current energy value to track from zero
            state = self._hass.states.get(self._source_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._last_energy = float(state.state)
                except (ValueError, TypeError):
                    self._last_energy = 0.0
            else:
                self._last_energy = 0.0
                
            await self._save_state()
            self.safe_write_ha_state()

    async def _handle_state_change(self, event):
        """Update monthly energy when source changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        try:
            energy = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid energy value: {new_state.state}")
            return

        # If this is the first time we're getting a valid energy reading, initialise tracking
        if self._last_energy == 0.0:
            _debug_log(self.hass, f"Source energy sensor {self._source_sensor} became available, initialising monthly tracking for {self._attr_name}")
            self._last_energy = energy
            await self._save_state()
            self.safe_write_ha_state()
            return

        # Calculate the energy change
        energy_change = max(0, energy - self._last_energy)
        self._state += energy_change
        self._last_energy = energy
        
        await self._save_state()
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

    @property
    def native_value(self):
        """Return the current state."""
        return round(self._state, 4)  # Match main energy sensor precision

    @property
    def state(self):
        """Return the current state."""
        return round(self._state, 4)  # Match main energy sensor precision

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement, ensuring it's always kWh."""
        return UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_unit_of_measurement(self):
        """Return the native unit of measurement, ensuring it's always kWh."""
        return UnitOfEnergy.KILO_WATT_HOUR
        
    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            "last_reset": self._last_reset
        }

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

class WeeklyEnergySensor(SensorEntity, RestoreEntity):
	"""Custom sensor for weekly energy tracking (ISO week, resets Monday)."""

	def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
		"""Initialize the sensor."""
		self._hass = hass
		self._base_name = base_name
		self._source_sensor = source_sensor
		self._storage_manager: StorageManager | None = storage_path if isinstance(storage_path, StorageManager) else None
		self._storage_path = storage_path
		friendly_name = get_friendly_name_from_base(hass, base_name)
		proposed_name = f"{friendly_name} Weekly Energy"
		unique_name = get_unique_entity_name(hass, proposed_name)
		self._attr_unique_id = f"{base_name}_weekly_energy"
		self._attr_name = unique_name
		self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_device_class = SensorDeviceClass.ENERGY
		self._attr_state_class = SensorStateClass.TOTAL_INCREASING
		self._attr_entity_registry_enabled_default = True
		if device_identifiers:
			self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
		else:
			entity_registry = er.async_get(hass)
			source_entity = entity_registry.async_get(source_sensor)
			if source_entity and source_entity.device_id:
				device_registry = dr.async_get(hass)
				device = device_registry.async_get(source_entity.device_id)
				if device:
					self._attr_device_info = DeviceInfo(identifiers=device.identifiers)
		self._state = 0.0
		self._last_energy = 0.0
		self._last_reset = None
		self._storage_key = f"{base_name}_weekly_energy"

	async def _load_state(self):
		"""Load state from storage."""
		# Load once; if write fails due to FD exhaustion, skip instead of looping
		if self._storage_manager:
			storage = await self._storage_manager.async_load()
		else:
			from homeassistant.helpers import storage as ha_storage
			store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
			storage = await store.async_load() or {}
		state_data = storage.get(self._storage_key, {})
		self._state = state_data.get("value", 0.0)
		self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
		self._last_energy = state_data.get("last_energy", 0.0)

	async def _save_state(self):
		"""Save state to storage."""
		if self._storage_manager:
			storage = await self._storage_manager.async_load()
		else:
			from homeassistant.helpers import storage as ha_storage
			store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
			storage = await store.async_load() or {}
		storage[self._storage_key] = {
			"value": self._state,
			"last_reset": self._last_reset,
			"last_energy": self._last_energy
		}
		try:
			if self._storage_manager:
				await self._storage_manager.async_save(storage)
			else:
				from homeassistant.helpers import storage as ha_storage
				store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
				await store.async_save(storage)
		except Exception as e:
			_DEBUG = False
			try:
				_DEBUG = _is_debug_enabled(self._hass)
			except Exception:
				pass
			if _DEBUG:
				_LOGGER.warning(f"Skipping storage save due to error: {e}")

	async def async_added_to_hass(self):
		"""Handle entity addition."""
		await self._load_state()
		if self._state == 0.0:
			last = await self.async_get_last_state()
			try:
				if last and last.state not in ("unknown", "unavailable", None):
					self._state = float(last.state)
			except (ValueError, TypeError):
				pass
		self._unsub_state = async_track_state_change_event(
			self._hass, [self._source_sensor], self._handle_state_change
		)
		# Check each midnight whether it's Monday (ISO week start)
		self._unsub_week = async_track_time_change(
			self._hass,
			self._handle_week_reset,
			hour=0,
			minute=0,
			second=0
		)
		self.safe_write_ha_state()

	async def async_will_remove_from_hass(self):
		"""Clean up resources when entity is removed."""
		try:
			if hasattr(self, "_unsub_state") and self._unsub_state:
				self._unsub_state()
		except Exception:
			pass
		try:
			if hasattr(self, "_unsub_week") and self._unsub_week:
				self._unsub_week()
		except Exception:
			pass

	async def _handle_week_reset(self, now):
		"""Reset at the start of ISO week (Monday)."""
		if now.weekday() == 0:
			_LOGGER.info(f"Weekly reset for {self._attr_name}")
			self._state = 0.0
			self._last_reset = now.isoformat()
			state = self._hass.states.get(self._source_sensor)
			if state and state.state not in ("unknown", "unavailable"):
				try:
					self._last_energy = float(state.state)
				except (ValueError, TypeError):
					self._last_energy = 0.0
			else:
				self._last_energy = 0.0
			await self._save_state()
			self.safe_write_ha_state()

	async def _handle_state_change(self, event):
		"""Update weekly energy when source changes."""
		new_state = event.data.get("new_state")
		if new_state is None or new_state.state in ("unknown", "unavailable"):
			return
		try:
			energy = float(new_state.state)
		except ValueError:
			_LOGGER.warning(f"Invalid energy value: {new_state.state}")
			return
		if self._last_energy == 0.0:
			_debug_log(self.hass, f"Source energy sensor {self._source_sensor} became available, initialising weekly tracking for {self._attr_name}")
			self._last_energy = energy
			await self._save_state()
			self.safe_write_ha_state()
			return
		energy_change = max(0, energy - self._last_energy)
		self._state += energy_change
		self._last_energy = energy
		await self._save_state()
		self.safe_write_ha_state()

	@property
	def native_value(self):
		return round(self._state, 4)

	@property
	def state(self):
		return round(self._state, 4)

	@property
	def unit_of_measurement(self):
		return UnitOfEnergy.KILO_WATT_HOUR

	@property
	def native_unit_of_measurement(self):
		return UnitOfEnergy.KILO_WATT_HOUR

	@property
	def extra_state_attributes(self):
		return {
			"last_reset": self._last_reset
		}

	def safe_write_ha_state(self):
		try:
			if not hasattr(self, '_attr_unit_of_measurement') or not self._attr_unit_of_measurement:
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
				_LOGGER.warning(f"Unit of measurement was missing for {self._attr_name}, restored to kWh")
			if self._attr_unit_of_measurement != UnitOfEnergy.KILO_WATT_HOUR:
				_LOGGER.warning(f"Unit of measurement was incorrect for {self._attr_name} ({self._attr_unit_of_measurement}), correcting to kWh")
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
			self.async_write_ha_state()
		except Exception as e:
			_LOGGER.error(f"Error writing HA state for {self._attr_name}: {e}", exc_info=True)

class AnnualEnergySensor(SensorEntity, RestoreEntity):
	"""Custom sensor for annual energy tracking (resets 1 Jan)."""

	def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
		self._hass = hass
		self._base_name = base_name
		self._source_sensor = source_sensor
		self._storage_manager: StorageManager | None = storage_path if isinstance(storage_path, StorageManager) else None
		self._storage_path = storage_path
		friendly_name = get_friendly_name_from_base(hass, base_name)
		proposed_name = f"{friendly_name} Annual Energy"
		unique_name = get_unique_entity_name(hass, proposed_name)
		self._attr_unique_id = f"{base_name}_annual_energy"
		self._attr_name = unique_name
		self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_device_class = SensorDeviceClass.ENERGY
		self._attr_state_class = SensorStateClass.TOTAL_INCREASING
		self._attr_entity_registry_enabled_default = True
		if device_identifiers:
			self._attr_device_info = DeviceInfo(identifiers=device_identifiers)
		else:
			entity_registry = er.async_get(hass)
			source_entity = entity_registry.async_get(source_sensor)
			if source_entity and source_entity.device_id:
				device_registry = dr.async_get(hass)
				device = device_registry.async_get(source_entity.device_id)
				if device:
					self._attr_device_info = DeviceInfo(identifiers=device.identifiers)
		self._state = 0.0
		self._last_energy = 0.0
		self._last_reset = None
		self._storage_key = f"{base_name}_annual_energy"

	async def _load_state(self):
		if self._storage_manager:
			storage = await self._storage_manager.async_load()
		else:
			from homeassistant.helpers import storage as ha_storage
			store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
			storage = await store.async_load() or {}
		state_data = storage.get(self._storage_key, {})
		self._state = state_data.get("value", 0.0)
		self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
		self._last_energy = state_data.get("last_energy", 0.0)

	async def _save_state(self):
		if self._storage_manager:
			storage = await self._storage_manager.async_load()
		else:
			from homeassistant.helpers import storage as ha_storage
			store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
			storage = await store.async_load() or {}
		storage[self._storage_key] = {
			"value": self._state,
			"last_reset": self._last_reset,
			"last_energy": self._last_energy
		}
		if self._storage_manager:
			await self._storage_manager.async_save(storage)
		else:
			from homeassistant.helpers import storage as ha_storage
			store = ha_storage.Store(self._hass, version=1, key="energy_sensor_generator")
			await store.async_save(storage)

	async def async_added_to_hass(self):
		await self._load_state()
		if self._state == 0.0:
			last = await self.async_get_last_state()
			try:
				if last and last.state not in ("unknown", "unavailable", None):
					self._state = float(last.state)
			except (ValueError, TypeError):
				pass
		self._unsub_state = async_track_state_change_event(
			self._hass, [self._source_sensor], self._handle_state_change
		)
		# Check each midnight whether it's 1 Jan
		self._unsub_year = async_track_time_change(
			self._hass,
			self._handle_year_reset,
			hour=0,
			minute=0,
			second=0
		)
		self.safe_write_ha_state()

	async def async_will_remove_from_hass(self):
		"""Clean up resources when entity is removed."""
		try:
			if hasattr(self, "_unsub_state") and self._unsub_state:
				self._unsub_state()
		except Exception:
			pass
		try:
			if hasattr(self, "_unsub_year") and self._unsub_year:
				self._unsub_year()
		except Exception:
			pass

	async def _handle_year_reset(self, now):
		if now.month == 1 and now.day == 1:
			_LOGGER.info(f"Annual reset for {self._attr_name}")
			self._state = 0.0
			self._last_reset = now.isoformat()
			state = self._hass.states.get(self._source_sensor)
			if state and state.state not in ("unknown", "unavailable"):
				try:
					self._last_energy = float(state.state)
				except (ValueError, TypeError):
					self._last_energy = 0.0
			else:
				self._last_energy = 0.0
			await self._save_state()
			self.safe_write_ha_state()

	async def _handle_state_change(self, event):
		new_state = event.data.get("new_state")
		if new_state is None or new_state.state in ("unknown", "unavailable"):
			return
		try:
			energy = float(new_state.state)
		except ValueError:
			_LOGGER.warning(f"Invalid energy value: {new_state.state}")
			return
		if self._last_energy == 0.0:
			_debug_log(self.hass, f"Source energy sensor {self._source_sensor} became available, initialising annual tracking for {self._attr_name}")
			self._last_energy = energy
			await self._save_state()
			self.safe_write_ha_state()
			return
		energy_change = max(0, energy - self._last_energy)
		self._state += energy_change
		self._last_energy = energy
		await self._save_state()
		self.safe_write_ha_state()

	@property
	def native_value(self):
		return round(self._state, 4)

	@property
	def state(self):
		return round(self._state, 4)

	@property
	def unit_of_measurement(self):
		return UnitOfEnergy.KILO_WATT_HOUR

	@property
	def native_unit_of_measurement(self):
		return UnitOfEnergy.KILO_WATT_HOUR

	@property
	def extra_state_attributes(self):
		return {
			"last_reset": self._last_reset
		}

	def safe_write_ha_state(self):
		try:
			if not hasattr(self, '_attr_unit_of_measurement') or not self._attr_unit_of_measurement:
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
				_LOGGER.warning(f"Unit of measurement was missing for {self._attr_name}, restored to kWh")
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
