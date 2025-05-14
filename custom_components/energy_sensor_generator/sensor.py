import logging
from datetime import datetime, timedelta
from homeassistant.components.sensor import (
    SensorEntity, 
    SensorDeviceClass,
    SensorStateClass
)
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from .utils import load_storage, save_storage
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform."""
    hass.data[DOMAIN][entry.entry_id]["async_add_entities"] = async_add_entities
    return

class EnergySensor(SensorEntity):
    """Custom sensor to calculate kWh from power (Watts)."""

    def __init__(self, hass, base_name, source_sensor, storage_path):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_energy"
        self._attr_name = f"{base_name} Energy"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
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
        self._last_power = None
        self._last_update = None
        self._storage_key = f"{base_name}_energy"
        self._load_state()

    def _load_state(self):
        """Load state from storage."""
        storage = load_storage(self._storage_path)
        self._state = storage.get(self._storage_key, {}).get("value", 0.0)

    def _save_state(self):
        """Save state to storage."""
        storage = load_storage(self._storage_path)
        storage[self._storage_key] = {"value": self._state}
        save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )

    async def _handle_state_change(self, event):
        """Update energy when power changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        now = datetime.now()
        try:
            power = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid power value: {new_state.state}")
            return

        if self._last_power is not None and self._last_update is not None:
            # Calculate time delta in hours
            delta_hours = (now - self._last_update).total_seconds() / 3600
            # Trapezoidal rule: average power * time (kWh)
            avg_power = (self._last_power + power) / 2
            energy_kwh = (avg_power * delta_hours) / 1000
            self._state += energy_kwh
            self._save_state()

        self._last_power = power
        self._last_update = now
        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the current state."""
        return round(self._state, 2)

    @property
    def state(self):
        """Return the current state."""
        return round(self._state, 2)

class DailyEnergySensor(SensorEntity):
    """Custom sensor for daily energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path):
        """Initialize the sensor."""
        self._hass = hass
        self._base_name = base_name
        self._source_sensor = source_sensor
        self._storage_path = storage_path
        
        # Generate entity attributes
        self._attr_unique_id = f"{base_name}_daily_energy"
        self._attr_name = f"{base_name} Daily Energy"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
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
        self._last_reset = None
        self._storage_key = f"{base_name}_daily_energy"
        self._load_state()

    def _load_state(self):
        """Load state from storage."""
        storage = load_storage(self._storage_path)
        state_data = storage.get(self._storage_key, {})
        self._state = state_data.get("value", 0.0)
        self._last_reset = state_data.get("last_reset", datetime.now().isoformat())

    def _save_state(self):
        """Save state to storage."""
        storage = load_storage(self._storage_path)
        storage[self._storage_key] = {
            "value": self._state,
            "last_reset": self._last_reset
        }
        save_storage(self._storage_path, storage)

    async def async_added_to_hass(self):
        """Handle entity addition."""
        async_track_state_change_event(
            self._hass, [self._source_sensor], self._handle_state_change
        )

    async def _handle_state_change(self, event):
        """Update daily energy when source changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        now = datetime.now()
        try:
            energy = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid energy value: {new_state.state}")
            return

        # Check for daily reset
        last_reset = datetime.fromisoformat(self._last_reset)
        if now.date() > last_reset.date():
            self._state = 0.0
            self._last_reset = now.isoformat()
            _LOGGER.info(f"Reset daily energy for {self._base_name}")

        self._state = energy
        self._save_state()
        self.async_write_ha_state()

    @property
    def native_value(self):
        """Return the current state."""
        return round(self._state, 2)

    @property
    def state(self):
        """Return the current state."""
        return round(self._state, 2)

class MonthlyEnergySensor(DailyEnergySensor):
    """Custom sensor for monthly energy tracking."""

    def __init__(self, hass, base_name, source_sensor, storage_path):
        """Initialize the sensor."""
        super().__init__(hass, base_name, source_sensor, storage_path)
        self._attr_unique_id = f"{base_name}_monthly_energy"
        self._attr_name = f"{base_name} Monthly Energy"
        self._storage_key = f"{base_name}_monthly_energy"

    async def _handle_state_change(self, event):
        """Update monthly energy when source changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        now = datetime.now()
        try:
            energy = float(new_state.state)
        except ValueError:
            _LOGGER.warning(f"Invalid energy value: {new_state.state}")
            return

        # Check for monthly reset
        last_reset = datetime.fromisoformat(self._last_reset)
        if now.month > last_reset.month or now.year > last_reset.year:
            self._state = 0.0
            self._last_reset = now.isoformat()
            _LOGGER.info(f"Reset monthly energy for {self._base_name}")

        self._state = energy
        self._save_state()
        self.async_write_ha_state()
