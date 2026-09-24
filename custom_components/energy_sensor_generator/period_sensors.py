"""Period (daily/weekly/monthly/annual) energy sensors.

Each one tracks the increase of a generated main energy sensor and resets at
its period boundary, replacing the utility_meter helper.
"""
import logging

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
	SensorEntity,
	SensorDeviceClass,
	SensorStateClass
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .entity_helpers import (
	debug_log,
	get_friendly_name_from_base,
	get_unique_entity_name,
	persist_storage_key,
)
from .naming import period_start, period_unique_id
from .utils import StorageManager

_LOGGER = logging.getLogger(__name__)


class PeriodEnergySensor(SensorEntity, RestoreEntity):
	"""Base class for period energy sensors (daily/weekly/monthly/annual).

	Tracks the increase of a generated main energy sensor and resets at the
	period boundary. Subclasses define the period label and unique_id suffix.

	The main sensor is found through its unique ID in the entity registry, so
	tracking survives entity ID renames and does not depend on how Home
	Assistant slugified the main sensor's name.
	"""

	PERIOD_LABEL = ""  # e.g. "Daily" - used in the friendly name
	PERIOD_SUFFIX = ""  # e.g. "daily" - used in unique_id and storage key

	def __init__(self, hass, base_name, main_unique_id, storage_path, device_identifiers=None):
		"""Initialize the sensor."""
		assert self.PERIOD_LABEL and self.PERIOD_SUFFIX, "Subclasses must define period metadata"
		self._hass = hass
		self._base_name = base_name
		self._main_unique_id = main_unique_id
		self._source_sensor = None  # main sensor entity_id, resolved once added
		# Prefer StorageManager if provided
		self._storage_manager: StorageManager | None = storage_path if isinstance(storage_path, StorageManager) else None
		self._storage_path = storage_path

		# Derive friendly name from base_name since the source_sensor is the
		# generated energy sensor, not the original power sensor.
		friendly_name = get_friendly_name_from_base(hass, base_name)
		proposed_name = f"{friendly_name} {self.PERIOD_LABEL} Energy"
		self._attr_name = get_unique_entity_name(hass, proposed_name)
		self._attr_unique_id = period_unique_id(base_name, self.PERIOD_SUFFIX)
		self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
		self._attr_device_class = SensorDeviceClass.ENERGY
		self._attr_state_class = SensorStateClass.TOTAL_INCREASING
		self._attr_entity_registry_enabled_default = True

		# Same device as the main sensor; sources without a device share the
		# fallback device the main sensor creates for this base name.
		self._attr_device_info = DeviceInfo(
			identifiers=device_identifiers or {(DOMAIN, base_name)}
		)

		self._state = 0.0
		self._last_energy = 0.0
		self._last_reset = None
		self._storage_key = self._attr_unique_id
		self._unsub_state = None
		# State will be loaded in async_added_to_hass

	@property
	def total(self) -> float:
		"""Unrounded running total in kWh."""
		return float(self._state)

	@property
	def storage_key(self) -> str:
		return self._storage_key

	def _should_reset(self, now) -> bool:
		"""Return True when ``now`` (local midnight) starts a new period."""
		midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
		return period_start(self.PERIOD_SUFFIX, now) == midnight

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
		await persist_storage_key(self._hass, self._storage_manager, self._storage_key, {
			"value": self._state,
			"last_reset": self._last_reset,
			"last_energy": self._last_energy
		}, self._attr_name)

	async def async_added_to_hass(self):
		"""Handle entity addition."""
		# Load state from storage first; fall back to the HA restore cache
		await self._load_state()
		if self._state == 0.0:
			last = await self.async_get_last_state()
			try:
				if last and last.state not in ("unknown", "unavailable", None):
					self._state = float(last.state)
			except (ValueError, TypeError):
				pass

		# A reset is missed when Home Assistant is not running at midnight;
		# catch up now so the period does not carry the previous one's total.
		await self._async_catch_up_missed_reset()

		self._async_track_main_sensor()
		self.async_on_remove(self._async_untrack_main_sensor)
		self.async_on_remove(
			self._hass.bus.async_listen(
				er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry_updated
			)
		)

		# Check the period boundary at midnight each day
		self.async_on_remove(
			async_track_time_change(
				self._hass,
				self._handle_period_reset,
				hour=0,
				minute=0,
				second=0
			)
		)
		self.safe_write_ha_state()

	@callback
	def _async_track_main_sensor(self) -> None:
		"""Follow the main sensor's current entity_id (it can be renamed)."""
		entity_id = er.async_get(self._hass).async_get_entity_id("sensor", DOMAIN, self._main_unique_id)
		if entity_id == self._source_sensor:
			return
		self._async_untrack_main_sensor()
		self._source_sensor = entity_id
		if entity_id:
			self._unsub_state = async_track_state_change_event(
				self._hass, [entity_id], self._handle_state_change
			)

	@callback
	def _async_untrack_main_sensor(self) -> None:
		if self._unsub_state:
			self._unsub_state()
			self._unsub_state = None

	@callback
	def _handle_registry_updated(self, event) -> None:
		if event.data.get("action") in ("create", "update"):
			self._async_track_main_sensor()

	async def _async_catch_up_missed_reset(self) -> None:
		last_reset = dt_util.parse_datetime(self._last_reset) if isinstance(self._last_reset, str) else None
		if last_reset is None:
			return
		if last_reset.tzinfo is None:
			last_reset = dt_util.as_utc(last_reset)
		start = period_start(self.PERIOD_SUFFIX, dt_util.now())
		if last_reset >= start:
			return
		_LOGGER.info(
			"%s reset for %s was missed while Home Assistant was offline (last reset %s); resetting now",
			self.PERIOD_LABEL,
			self._attr_name,
			self._last_reset,
		)
		# _last_energy is kept: energy after this point belongs to the new period.
		self._state = 0.0
		self._last_reset = start.isoformat()
		await self._save_state()

	async def _handle_period_reset(self, now):
		"""Reset the counter when the period boundary is reached."""
		if not self._should_reset(now):
			return
		_LOGGER.info(f"{self.PERIOD_LABEL} reset for {self._attr_name}")
		self._state = 0.0
		self._last_reset = now.isoformat()
		# Re-anchor tracking to the main sensor's current value. If it is
		# unavailable, keep the previous anchor rather than zeroing it, which
		# would make the next reading look like a first reading.
		state = self._hass.states.get(self._source_sensor) if self._source_sensor else None
		if state and state.state not in ("unknown", "unavailable"):
			try:
				self._last_energy = float(state.state)
			except (ValueError, TypeError):
				pass
		await self._save_state()
		self.safe_write_ha_state()

	async def async_set_total(self, value: float) -> None:
		"""Set this period's total (used by the adjustment services)."""
		self._state = float(value)
		await self._save_state()
		self.safe_write_ha_state()

	async def async_reload_from_storage(self) -> None:
		"""Re-read state after storage was replaced (import service)."""
		await self._load_state()
		self.safe_write_ha_state()

	async def _handle_state_change(self, event):
		"""Accumulate the source energy sensor's increase."""
		new_state = event.data.get("new_state")
		if new_state is None or new_state.state in ("unknown", "unavailable"):
			return
		try:
			energy = float(new_state.state)
		except (TypeError, ValueError):
			_LOGGER.warning(f"Invalid energy value: {new_state.state}")
			return

		# If this is the first valid reading, initialise tracking
		if self._last_energy == 0.0:
			debug_log(self.hass, f"Source energy sensor {self._source_sensor} became available, initialising {self.PERIOD_SUFFIX} tracking for {self._attr_name}")
			self._last_energy = energy
			await self._save_state()
			self.safe_write_ha_state()
			return

		# Only count increases; a decrease means the source was corrected/reset
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
			if not getattr(self, '_attr_unit_of_measurement', None):
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
				_LOGGER.warning(f"Unit of measurement was missing for {self._attr_name}, restored to kWh")
			if self._attr_unit_of_measurement != UnitOfEnergy.KILO_WATT_HOUR:
				_LOGGER.warning(f"Unit of measurement was incorrect for {self._attr_name} ({self._attr_unit_of_measurement}), correcting to kWh")
				self._attr_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
			self.async_write_ha_state()
		except Exception as e:
			_LOGGER.error(f"Error writing HA state for {self._attr_name}: {e}", exc_info=True)


class DailyEnergySensor(PeriodEnergySensor):
	"""Daily energy tracking; resets at midnight."""

	PERIOD_LABEL = "Daily"
	PERIOD_SUFFIX = "daily"


class MonthlyEnergySensor(PeriodEnergySensor):
	"""Monthly energy tracking; resets on the first day of the month."""

	PERIOD_LABEL = "Monthly"
	PERIOD_SUFFIX = "monthly"


class WeeklyEnergySensor(PeriodEnergySensor):
	"""Weekly energy tracking (ISO week); resets on Monday."""

	PERIOD_LABEL = "Weekly"
	PERIOD_SUFFIX = "weekly"


class AnnualEnergySensor(PeriodEnergySensor):
	"""Annual energy tracking; resets on 1 January."""

	PERIOD_LABEL = "Annual"
	PERIOD_SUFFIX = "annual"


PERIOD_SENSOR_CLASSES = {
	cls.PERIOD_SUFFIX: cls
	for cls in (DailyEnergySensor, WeeklyEnergySensor, MonthlyEnergySensor, AnnualEnergySensor)
}
