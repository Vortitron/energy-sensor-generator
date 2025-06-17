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
try:
    from homeassistant.components import recorder
    from homeassistant.components.recorder import statistics
    STATISTICS_AVAILABLE = True
except ImportError:
    STATISTICS_AVAILABLE = False
    _LOGGER.warning("Statistics module not available, using point sampling only")
from .utils import load_storage, save_storage
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

def get_friendly_name(hass: HomeAssistant, entity_id: str) -> str:
	"""Get the friendly name for an entity, falling back to derived name from entity ID."""
	entity_registry = er.async_get(hass)
	entity_entry = entity_registry.async_get(entity_id)
	
	# Try to get custom name from entity registry first
	if entity_entry and entity_entry.name:
		# Remove "_power" suffix if present to get clean base name
		name = entity_entry.name
		if name.lower().endswith(" power"):
			name = name[:-6]  # Remove " power"
		elif name.lower().endswith("_power"):
			name = name[:-6]  # Remove "_power"
		return name
	
	# Try to get friendly_name from entity state
	state = hass.states.get(entity_id)
	if state and state.attributes.get("friendly_name"):
		name = state.attributes["friendly_name"]
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
	# Try the most common pattern first
	possible_sensors = [
		f"sensor.{base_name}_power",
		f"sensor.{base_name}",
		f"{base_name}_power",
		f"{base_name}"
	]
	
	for sensor_id in possible_sensors:
		if hass.states.get(sensor_id):
			return get_friendly_name(hass, sensor_id)
	
	# If no sensor found, just clean up the base_name
	return base_name.replace("_", " ").title()

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
			if entity_id.startswith(f"{domain}.") and (
				(entry.name and entry.name.lower() == proposed_name.lower()) or
				(entry.original_name and entry.original_name.lower() == proposed_name.lower())
			):
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
	hass.data[DOMAIN][entry.entry_id]["async_add_entities"] = async_add_entities
	
	# Check if we need to recreate existing entities during reload
	options = entry.options
	
	# Only proceed if we have selected sensors configured
	selected_sensors = options.get("selected_power_sensors", [])
	if not selected_sensors:
		return
	
	# Find existing generated sensors
	entity_registry = er.async_get(hass)
	existing_entities = []
	
	# Look for entities with this integration's platform
	for entity_id, entity_entry in entity_registry.entities.items():
		if entity_entry.platform == DOMAIN and entity_entry.config_entry_id == entry.entry_id:
			existing_entities.append((entity_id, entity_entry.unique_id))
	
	# If we have existing entities, recreate them to ensure they're properly linked
	if existing_entities:
		_LOGGER.info(f"Found {len(existing_entities)} existing energy sensors to recreate during setup")
		
		# Get storage path
		from .const import STORAGE_FILE
		from pathlib import Path
		storage_path = Path(hass.config.path(STORAGE_FILE))
		
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
			source_sensor = f"sensor.{base_name}_power"
			if source_sensor not in selected_sensors:
				# Try to find the actual source sensor from selected sensors
				for selected in selected_sensors:
					selected_base = selected.replace("sensor.", "").replace("_power", "")
					if selected_base == base_name:
						source_sensor = selected
						break
			
			# Check if source sensor still exists
			if hass.states.get(source_sensor) is None:
				_LOGGER.info(f"Source sensor {source_sensor} not yet available during startup for {base_name}, will create energy sensor anyway")
				# During startup, we'll proceed anyway - the sensor should handle unavailable source gracefully
			
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
				energy_sensor = EnergySensor(hass, base_name, source_sensor, storage_path, device_identifiers)
				entities_to_add.append(energy_sensor)
				_LOGGER.debug(f"Recreated main energy sensor for {base_name}")
			
			# Recreate daily sensor if it exists
			if "daily" in sensor_types:
				daily_sensor = DailyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_path, device_identifiers)
				entities_to_add.append(daily_sensor)
				_LOGGER.debug(f"Recreated daily energy sensor for {base_name}")
			
			# Recreate monthly sensor if it exists  
			if "monthly" in sensor_types:
				monthly_sensor = MonthlyEnergySensor(hass, base_name, f"sensor.{base_name}_energy", storage_path, device_identifiers)
				entities_to_add.append(monthly_sensor)
				_LOGGER.debug(f"Recreated monthly energy sensor for {base_name}")
		
		# Add all recreated entities
		if entities_to_add:
			async_add_entities(entities_to_add, True)  # True = update_before_add
			_LOGGER.info(f"Successfully recreated {len(entities_to_add)} energy sensors during setup")
	
	return

class EnergySensor(SensorEntity):
    """Custom sensor to calculate kWh from power (Watts)."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        self._device_identifiers = device_identifiers
        
        # Sensor attributes
        self._attr_name = f"{base_name.replace('_', ' ').title()} Energy"
        self._attr_unique_id = f"{base_name}_energy"
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:flash"
        
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
        """Get statistical power data from Home Assistant's recorder."""
        try:
            if not STATISTICS_AVAILABLE:
                _LOGGER.debug(f"Statistics module not available for {self._attr_name}")
                return None
                
            if not recorder.get_instance(self._hass):
                _LOGGER.debug(f"Recorder not available for {self._attr_name}, falling back to point sampling")
                return None
            
            # Calculate time difference for appropriate period selection
            time_diff = (end_time - start_time).total_seconds()
            period = "5minute" if time_diff < 3600 else "hour"  # Use 5-minute stats for shorter periods
            
            _LOGGER.debug(f"Getting statistical data for {self._source_sensor}, period: {period}, time range: {time_diff:.0f}s")
                
            # Get statistics for the power sensor over the time period
            stats = await statistics.statistics_during_period(
                self._hass,
                start_time,
                end_time,
                [self._source_sensor],
                period,
                units=None
            )
            
            if not stats or self._source_sensor not in stats:
                _LOGGER.debug(f"No statistical data available for {self._source_sensor} in period {period}")
                return None
                
            sensor_stats = stats[self._source_sensor]
            if not sensor_stats:
                _LOGGER.debug(f"Empty statistics for {self._source_sensor}")
                return None
                
            # Calculate total energy from statistical mean values
            total_energy_kwh = 0.0
            period_hours = 1/12 if period == "5minute" else 1  # 5 minutes = 1/12 hour
            
            for stat in sensor_stats:
                if stat.get("mean") is not None:
                    mean_power = float(stat["mean"])
                    # Calculate energy for this period
                    energy_kwh = (mean_power * period_hours) / self._power_to_kw_factor
                    total_energy_kwh += energy_kwh
                    
            _LOGGER.info(f"Statistical calculation for {self._attr_name}: {len(sensor_stats)} {period} periods, total energy: {total_energy_kwh:.6f}kWh")
            return total_energy_kwh
            
        except Exception as e:
            _LOGGER.warning(f"Error getting statistical data for {self._source_sensor}: {e}", exc_info=True)
            return None

    async def _load_state(self):
        """Load state from storage."""
        storage = await load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        
        if isinstance(state_data, dict):
            self._state = state_data.get("value", 0.0)
            last_power = state_data.get("last_power")
            last_update = state_data.get("last_update")
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
        else:
            # Legacy format where state_data is just a float
            legacy_value = float(state_data) if state_data else 0.0
            current_conversion_factor = self._get_power_conversion_factor(self._hass, self._source_sensor)
            
            self._state = legacy_value
            self._power_to_kw_factor = current_conversion_factor

    async def _save_state(self):
        """Save state to storage."""
        storage = await load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_power": self._last_power,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "conversion_factor": self._power_to_kw_factor
        }
        await save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Load state from storage first
        await self._load_state()
        
        # Track state changes to the power sensor
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Get sampling interval from options
        sample_interval = 60  # Default 60 seconds if not specified
        
        # Try to get the configured sample interval from the integration's options
        for entry_id, entry_data in self._hass.data[DOMAIN].items():
            if "options" in entry_data:
                sample_interval = entry_data["options"].get("sample_interval", 60)
                break
        
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
                _LOGGER.info(f"Source sensor {self._source_sensor} not yet available for {self._attr_name} - will initialise when available")
        
        # Set up regular sampling interval for reliable energy calculation
        self._interval_tracker = async_track_time_interval(
            self._hass,
            self._handle_interval_update,
            timedelta(seconds=sample_interval)
        )
        
        # Also set up a midnight update to ensure we get regular updates
        async_track_time_change(
            self._hass,
            self._handle_midnight_update,
            hour=0,
            minute=0,
            second=0
        )

    async def _handle_interval_update(self, now):
        """Update energy calculation at regular intervals using statistical data when possible."""
        _LOGGER.warning(f"DEBUG: Interval update called for {self._attr_name}")
        
        if self._calculating_energy:
            _LOGGER.warning(f"DEBUG: Already calculating energy for {self._attr_name}, skipping")
            return
            
        self._calculating_energy = True
        try:
            # Ensure conversion factor is set
            self._ensure_conversion_factor()
            
            # Safety check for conversion factor
            if not self._power_to_kw_factor or self._power_to_kw_factor <= 0:
                _LOGGER.debug(f"Conversion factor not yet available for {self._source_sensor}, skipping calculation")
                return
            
            # Try statistical calculation first if we have previous data and it's enabled
            statistical_energy = None
            use_statistical = True  # Default to True for better accuracy
            
            # Check if statistical calculation is disabled in options
            for entry_id, entry_data in self._hass.data[DOMAIN].items():
                if "options" in entry_data:
                    use_statistical = entry_data["options"].get("use_statistical_calculation", True)
                    break
            
            _LOGGER.warning(f"DEBUG: Statistical calculation enabled: {use_statistical}, available: {STATISTICS_AVAILABLE}")
            
            # Try statistical calculation if enabled and available
            statistical_energy = None
            if use_statistical and STATISTICS_AVAILABLE:
                try:
                    statistical_energy = await self._get_statistical_power_data(self._last_update or (now - timedelta(minutes=5)), now)
                    if statistical_energy is not None:
                        _LOGGER.warning(f"DEBUG: Statistical calculation successful for {self._attr_name}: {statistical_energy:.6f}kWh")
                    else:
                        _LOGGER.warning(f"DEBUG: Statistical calculation returned None for {self._attr_name}, falling back to point sampling")
                except Exception as e:
                    _LOGGER.warning(f"DEBUG: Statistical calculation failed for {self._attr_name}: {e}, falling back to point sampling")
                    statistical_energy = None
            else:
                _LOGGER.warning(f"DEBUG: Using point sampling for {self._attr_name} (statistical: {use_statistical}, available: {STATISTICS_AVAILABLE})")
            
            # Get current state for fallback and tracking
            state = self._hass.states.get(self._source_sensor)
            if not state or state.state in ("unknown", "unavailable"):
                return
                
            try:
                power = float(state.state)
            except (ValueError, TypeError):
                _LOGGER.warning(f"Invalid power value: {state.state}")
                return
            
            # Use statistical data if available, otherwise fall back to trapezoidal rule
            if statistical_energy is not None and statistical_energy > 0:
                # Sanity check: ensure statistical energy isn't unreasonably high
                time_delta_hours = (now - self._last_update).total_seconds() / 3600
                max_possible_energy = (3000 * time_delta_hours) / self._power_to_kw_factor  # Assume max 3kW device
                
                if statistical_energy > max_possible_energy:
                    _LOGGER.warning(f"Statistical energy seems too high for {self._attr_name}: {statistical_energy:.6f}kWh vs max possible {max_possible_energy:.6f}kWh, falling back to point sampling")
                    statistical_energy = None
                else:
                    # Use statistical calculation
                    self._state += statistical_energy
                    self._using_statistical = True
                    unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                    self._calculation_count += 1
                    
                    # Log first successful calculation 
                    if not self._first_calculation_logged:
                        _LOGGER.info(f"Energy sensor {self._attr_name} is now tracking energy from {self._source_sensor} ({unit_display} sensor) using statistical data")
                        self._first_calculation_logged = True
                    
                    _LOGGER.info(f"Statistical energy calculation: {self._attr_name} | Energy added: {statistical_energy:.6f}kWh | Total: {self._state:.4f}kWh | Current power: {power:.2f}{unit_display} | Time delta: {(now - self._last_update).total_seconds():.0f}s")
            
            if statistical_energy is None and self._last_power is not None and self._last_update is not None:
                # Fall back to trapezoidal rule
                time_delta = (now - self._last_update).total_seconds()
                delta_hours = time_delta / 3600
                
                _LOGGER.warning(f"DEBUG: Point sampling calculation for {self._attr_name} | Last power: {self._last_power} | Current power: {power} | Time delta: {time_delta:.0f}s")
                
                # Trapezoidal rule: average power * time (kWh)
                avg_power = (self._last_power + power) / 2
                energy_kwh = (avg_power * delta_hours) / self._power_to_kw_factor
                
                _LOGGER.warning(f"DEBUG: Calculated energy: {energy_kwh:.8f}kWh | Avg power: {avg_power:.4f} | Delta hours: {delta_hours:.6f} | Conversion factor: {self._power_to_kw_factor}")
                
                # Ensure we're not adding negative energy
                if energy_kwh > 0:
                    self._state += energy_kwh
                    self._using_statistical = False
                    unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                    self._calculation_count += 1
                    
                    # Log first successful calculation 
                    if not self._first_calculation_logged:
                        _LOGGER.warning(f"Energy sensor {self._attr_name} is now tracking energy from {self._source_sensor} ({unit_display} sensor) using point sampling")
                        self._first_calculation_logged = True
                    
                    _LOGGER.warning(f"Point sampling: {self._attr_name} | Energy added: {energy_kwh:.8f}kWh | Total: {self._state:.4f}kWh")
                else:
                    unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                    _LOGGER.warning(f"DEBUG: No energy added (too small): avg power: {avg_power:.4f}{unit_display}, calculated energy: {energy_kwh:.8f}kWh")
            else:
                _LOGGER.debug(f"Interval update: Skipping calculation - missing previous power/time data for {self._attr_name}")
            
            # Update values
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
        if self._calculating_energy:
            return
            
        self._calculating_energy = True
        try:
            state = self._hass.states.get(self._source_sensor)
            if not state or state.state in ("unknown", "unavailable"):
                return
                
            try:
                power = float(state.state)
            except (ValueError, TypeError):
                _LOGGER.warning(f"Invalid power value: {state.state}")
                return
                
            if self._last_power is not None and self._last_update is not None:
                # Calculate time delta in hours since last update
                time_delta = (now - self._last_update).total_seconds()
                delta_hours = time_delta / 3600
                
                # Ensure conversion factor is set
                self._ensure_conversion_factor()
                
                # Safety check for conversion factor
                if not self._power_to_kw_factor or self._power_to_kw_factor <= 0:
                    _LOGGER.debug(f"Conversion factor not yet available for {self._source_sensor}, skipping midnight calculation")
                    return
                
                # Calculate energy since last update
                avg_power = (self._last_power + power) / 2
                energy_kwh = (avg_power * delta_hours) / self._power_to_kw_factor
                
                if energy_kwh > 0:
                    self._state += energy_kwh
                    unit_display = "kW" if self._power_to_kw_factor == 1 else "W"
                    _LOGGER.debug(f"Midnight calculation: {self._attr_name} | Power: {power:.2f}{unit_display} | Energy added: {energy_kwh:.6f}kWh | Total: {self._state:.3f}kWh")
                
                # Update values
                self._last_power = power
                self._last_update = now
                await self._save_state()
                self.safe_write_ha_state()
        except Exception as e:
            _LOGGER.error(f"Error during midnight calculation for {self._attr_name}: {e}", exc_info=True)
        finally:
            self._calculating_energy = False

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
            _LOGGER.info(f"Source sensor {self._source_sensor} state change detected, initialising tracking for {self._attr_name}")
            self._last_power = power
            self._last_update = now
            await self._save_state()
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
        # Cancel interval tracking
        if self._interval_tracker:
            self._interval_tracker()
            self._interval_tracker = None
        
        # Save state one last time
        await self._save_state()

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
        
        # Check if statistical calculation is enabled
        use_statistical = True
        for entry_id, entry_data in self._hass.data[DOMAIN].items():
            if "options" in entry_data:
                use_statistical = entry_data["options"].get("use_statistical_calculation", True)
                break
        attrs["statistical_calculation_enabled"] = use_statistical
        
        source_state = self._hass.states.get(self._source_sensor)
        if source_state:
            attrs["source_current_value"] = source_state.state
            attrs["source_unit_of_measurement"] = source_state.attributes.get("unit_of_measurement", "")
        
        # Get interval from options
        sample_interval = 60  # Default 
        # Try to get the configured sample interval from the integration's options
        for entry_id, entry_data in self._hass.data[DOMAIN].items():
            if "options" in entry_data:
                sample_interval = entry_data["options"].get("sample_interval", 60)
                break
                
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

class DailyEnergySensor(SensorEntity):
    """Custom sensor for daily energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
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
        storage = await load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
        self._last_energy = state_data.get("last_energy", 0.0)

    async def _save_state(self):
        """Save state to storage."""
        storage = await load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset,
            "last_energy": self._last_energy
        }
        await save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Load state from storage first
        await self._load_state()
        
        # Track state changes to the power sensor
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Set up midnight reset
        async_track_time_change(
            self._hass,
            self._handle_midnight_reset,
            hour=0,
            minute=0,
            second=0
        )

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
            _LOGGER.info(f"Source energy sensor {self._source_sensor} became available, initialising daily tracking for {self._attr_name}")
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

class MonthlyEnergySensor(SensorEntity):
    """Custom sensor for monthly energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path, device_identifiers=None):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
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
        storage = await load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", dt_util.utcnow().isoformat())
        self._last_energy = state_data.get("last_energy", 0.0)

    async def _save_state(self):
        """Save state to storage."""
        storage = await load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset,
            "last_energy": self._last_energy
        }
        await save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        # Load state from storage first
        await self._load_state()
        
        # Track state changes to the power sensor
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )
        
        # Set up first-of-month reset (check at midnight each day)
        async_track_time_change(
            self._hass,
            self._handle_month_reset,
            hour=0,
            minute=0,
            second=0
        )

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
            _LOGGER.info(f"Source energy sensor {self._source_sensor} became available, initialising monthly tracking for {self._attr_name}")
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
