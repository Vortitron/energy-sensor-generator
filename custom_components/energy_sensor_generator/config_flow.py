"""Config, subentry and options flows for Energy Sensor Generator.

Each tracked source is a config *subentry*, so the integration page shows
"Add energy sensor", "Add constant load" and "Add price add-on" buttons and
lists every source with its own entities. Adding a source is a short form
with a searchable entity picker limited to sensors that actually report
power. Global calculation settings live in the options ("Configure") flow.
"""
from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
	ConfigEntry,
	ConfigFlow,
	ConfigFlowResult,
	ConfigSubentryData,
	ConfigSubentryFlow,
	OptionsFlow,
	SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
	BooleanSelector,
	EntitySelector,
	EntitySelectorConfig,
	NumberSelector,
	NumberSelectorConfig,
	NumberSelectorMode,
	SelectSelector,
	SelectSelectorConfig,
	SelectSelectorMode,
	TextSelector,
	TextSelectorConfig,
)

from .const import (
	CONF_ADD_AMOUNT,
	CONF_CREATE_SYNTHETIC_GRID_TOTAL,
	CONF_DEBUG_LOGGING,
	CONF_FORCE_STATISTICAL_ONLY,
	CONF_MAX_ENERGY_PER_HOUR,
	CONF_NAME,
	CONF_PERIODS,
	CONF_POWER_W,
	CONF_PRICE_ID,
	CONF_SAMPLE_INTERVAL,
	CONF_SOURCE_ENTITY_ID,
	CONF_STAT_LOOKBACK_MINUTES,
	CONF_SWITCH_ENTITY_ID,
	CONF_USE_STATISTICAL,
	DEFAULT_SAMPLE_INTERVAL,
	DEFAULT_STAT_LOOKBACK_MINUTES,
	DOMAIN,
)
from .entity_helpers import get_friendly_name
from .naming import (
	PERIODS,
	SUBENTRY_CONSTANT_LOAD,
	SUBENTRY_POWER_SENSOR,
	SUBENTRY_PRICE_ADJUST,
	normalise_periods,
	price_title,
	subentry_unique_id,
)

PERIOD_SELECTOR = SelectSelector(
	SelectSelectorConfig(
		options=list(PERIODS),
		multiple=True,
		mode=SelectSelectorMode.LIST,
		translation_key="periods",
	)
)
NAME_SELECTOR = TextSelector(TextSelectorConfig(multiline=False))
POWER_W_SELECTOR = NumberSelector(
	NumberSelectorConfig(min=1, max=50000, step=1, unit_of_measurement="W", mode=NumberSelectorMode.BOX)
)
ADD_AMOUNT_SELECTOR = NumberSelector(
	NumberSelectorConfig(min=-100, max=100, step="any", mode=NumberSelectorMode.BOX)
)


def _detected_power_sensors(hass: HomeAssistant) -> list[str]:
	from . import detect_power_sensors

	return detect_power_sensors(hass)


def _price_sensors(hass: HomeAssistant) -> list[str]:
	"""Sensors that look like energy prices (unit per kWh / MWh, or monetary)."""
	found = []
	for state in hass.states.async_all("sensor"):
		unit = str(state.attributes.get("unit_of_measurement") or "").replace(" ", "").lower()
		if unit.endswith(("/kwh", "/mwh")) or state.attributes.get("device_class") == "monetary":
			found.append(state.entity_id)
	return found


def _configured_entities(entry: ConfigEntry, subentry_type: str, key: str) -> set[str]:
	"""Source entity IDs already used by subentries of one type."""
	return {
		str(subentry.data.get(key))
		for subentry in entry.subentries.values()
		if subentry.subentry_type == subentry_type and subentry.data.get(key)
	}


def _power_sensor_subentry(hass: HomeAssistant, entity_id: str, periods: Any) -> ConfigSubentryData:
	return ConfigSubentryData(
		subentry_type=SUBENTRY_POWER_SENSOR,
		title=get_friendly_name(hass, entity_id),
		unique_id=subentry_unique_id(SUBENTRY_POWER_SENSOR, entity_id),
		data={CONF_SOURCE_ENTITY_ID: entity_id, CONF_PERIODS: list(normalise_periods(periods))},
	)


def _clean_name(value: Any) -> str:
	return str(value or "").strip()


class EnergySensorGeneratorConfigFlow(ConfigFlow, domain=DOMAIN):
	"""Initial setup: pick the power sensors to track (more can be added later)."""

	VERSION = 2

	@classmethod
	@callback
	def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
		return {
			SUBENTRY_POWER_SENSOR: PowerSensorSubentryFlow,
			SUBENTRY_CONSTANT_LOAD: ConstantLoadSubentryFlow,
			SUBENTRY_PRICE_ADJUST: PriceAdjustSubentryFlow,
		}

	@staticmethod
	@callback
	def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
		return SettingsOptionsFlow()

	async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
		await self.async_set_unique_id(DOMAIN)
		self._abort_if_unique_id_configured()

		available = _detected_power_sensors(self.hass)

		if user_input is not None:
			chosen: list[str] = []
			for entity_id in user_input.get("power_sensors") or []:
				if entity_id and entity_id not in chosen:
					chosen.append(entity_id)
			subentries = [
				_power_sensor_subentry(self.hass, entity_id, user_input.get(CONF_PERIODS))
				for entity_id in chosen
			]
			return self.async_create_entry(
				title="Energy Sensor Generator",
				data={},
				options={},
				subentries=subentries,
			)

		schema: dict = {}
		if available:
			schema[vol.Optional("power_sensors", default=[])] = EntitySelector(
				EntitySelectorConfig(include_entities=available, multiple=True)
			)
		schema[vol.Optional(CONF_PERIODS, default=list(PERIODS))] = PERIOD_SELECTOR
		return self.async_show_form(
			step_id="user",
			data_schema=vol.Schema(schema),
			description_placeholders={"count": str(len(available))},
		)


class _SubentryFlowBase(ConfigSubentryFlow):
	"""Shared helpers; ``_get_entry`` only exists in Home Assistant 2025.4+."""

	def _entry(self) -> ConfigEntry:
		entry = self.hass.config_entries.async_get_entry(self.handler[0])
		assert entry is not None
		return entry


class PowerSensorSubentryFlow(_SubentryFlowBase):
	"""Add or edit one power sensor → kWh sensor."""

	async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
		entry = self._entry()
		configured = _configured_entities(entry, SUBENTRY_POWER_SENSOR, CONF_SOURCE_ENTITY_ID)
		available = [entity_id for entity_id in _detected_power_sensors(self.hass) if entity_id not in configured]
		errors: dict[str, str] = {}

		if user_input is not None:
			entity_id = user_input.get(CONF_SOURCE_ENTITY_ID)
			if not entity_id:
				errors[CONF_SOURCE_ENTITY_ID] = "no_sensor_selected"
			elif entity_id in configured:
				errors[CONF_SOURCE_ENTITY_ID] = "already_configured"
			else:
				subentry = _power_sensor_subentry(self.hass, entity_id, user_input.get(CONF_PERIODS))
				return self.async_create_entry(
					title=subentry["title"],
					data=subentry["data"],
					unique_id=subentry["unique_id"],
				)
		if not available:
			return self.async_abort(reason="no_power_sensors")

		return self.async_show_form(
			step_id="user",
			data_schema=vol.Schema({
				vol.Required(CONF_SOURCE_ENTITY_ID): EntitySelector(
					EntitySelectorConfig(include_entities=available)
				),
				vol.Optional(CONF_PERIODS, default=list(PERIODS)): PERIOD_SELECTOR,
			}),
			errors=errors,
			description_placeholders={"count": str(len(available))},
		)

	async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
		entry = self._entry()
		subentry = self._get_reconfigure_subentry()
		source = str(subentry.data.get(CONF_SOURCE_ENTITY_ID) or "")

		if user_input is not None:
			name = _clean_name(user_input.get(CONF_NAME))
			return self.async_update_and_abort(
				entry,
				subentry,
				title=name or get_friendly_name(self.hass, source),
				data_updates={
					CONF_PERIODS: list(normalise_periods(user_input.get(CONF_PERIODS))),
					CONF_NAME: name,
				},
			)

		return self.async_show_form(
			step_id="reconfigure",
			data_schema=vol.Schema({
				vol.Optional(CONF_NAME, default=str(subentry.data.get(CONF_NAME) or "")): NAME_SELECTOR,
				vol.Optional(
					CONF_PERIODS, default=list(normalise_periods(subentry.data.get(CONF_PERIODS)))
				): PERIOD_SELECTOR,
			}),
			description_placeholders={"source": source},
		)


class ConstantLoadSubentryFlow(_SubentryFlowBase):
	"""Add or edit a switch with a fixed wattage."""

	async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
		entry = self._entry()
		configured = _configured_entities(entry, SUBENTRY_CONSTANT_LOAD, CONF_SWITCH_ENTITY_ID)
		errors: dict[str, str] = {}

		if user_input is not None:
			switch = user_input.get(CONF_SWITCH_ENTITY_ID)
			power_w = user_input.get(CONF_POWER_W)
			if not switch:
				errors[CONF_SWITCH_ENTITY_ID] = "no_switch_selected"
			elif switch in configured:
				errors[CONF_SWITCH_ENTITY_ID] = "already_configured"
			elif not power_w or float(power_w) <= 0:
				errors[CONF_POWER_W] = "invalid_power"
			else:
				name = _clean_name(user_input.get(CONF_NAME))
				data = {
					CONF_SWITCH_ENTITY_ID: switch,
					CONF_POWER_W: float(power_w),
					CONF_PERIODS: list(normalise_periods(user_input.get(CONF_PERIODS))),
				}
				if name:
					data[CONF_NAME] = name
				return self.async_create_entry(
					title=name or get_friendly_name(self.hass, switch),
					data=data,
					unique_id=subentry_unique_id(SUBENTRY_CONSTANT_LOAD, switch),
				)

		return self.async_show_form(
			step_id="user",
			data_schema=vol.Schema({
				vol.Required(CONF_SWITCH_ENTITY_ID): EntitySelector(
					EntitySelectorConfig(domain=["switch", "input_boolean"], exclude_entities=sorted(configured))
				),
				# Optional so the box starts empty instead of at the minimum (1 W);
				# a missing value is reported as invalid_power above.
				vol.Optional(CONF_POWER_W): POWER_W_SELECTOR,
				vol.Optional(CONF_NAME): NAME_SELECTOR,
				vol.Optional(CONF_PERIODS, default=list(PERIODS)): PERIOD_SELECTOR,
			}),
			errors=errors,
		)

	async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
		entry = self._entry()
		subentry = self._get_reconfigure_subentry()
		switch = str(subentry.data.get(CONF_SWITCH_ENTITY_ID) or "")
		errors: dict[str, str] = {}

		if user_input is not None:
			power_w = user_input.get(CONF_POWER_W)
			if power_w is None or float(power_w) <= 0:
				errors[CONF_POWER_W] = "invalid_power"
			else:
				name = _clean_name(user_input.get(CONF_NAME))
				return self.async_update_and_abort(
					entry,
					subentry,
					title=name or get_friendly_name(self.hass, switch),
					data_updates={
						CONF_POWER_W: float(power_w),
						CONF_NAME: name,
						CONF_PERIODS: list(normalise_periods(user_input.get(CONF_PERIODS))),
					},
				)

		return self.async_show_form(
			step_id="reconfigure",
			data_schema=vol.Schema({
				vol.Required(CONF_POWER_W, default=subentry.data.get(CONF_POWER_W)): POWER_W_SELECTOR,
				vol.Optional(CONF_NAME, default=str(subentry.data.get(CONF_NAME) or "")): NAME_SELECTOR,
				vol.Optional(
					CONF_PERIODS, default=list(normalise_periods(subentry.data.get(CONF_PERIODS)))
				): PERIOD_SELECTOR,
			}),
			errors=errors,
			description_placeholders={"source": switch},
		)


def _price_picker_config(hass: HomeAssistant, configured: set[str]) -> EntitySelectorConfig:
	"""Offer price-like sensors only; fall back to every sensor when none are found."""
	candidates = [entity_id for entity_id in _price_sensors(hass) if entity_id not in configured]
	if candidates:
		return EntitySelectorConfig(include_entities=candidates)
	return EntitySelectorConfig(domain="sensor", exclude_entities=sorted(configured))


class PriceAdjustSubentryFlow(_SubentryFlowBase):
	"""Add or edit a price sensor mirror with a fixed add-on."""

	async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
		entry = self._entry()
		configured = _configured_entities(entry, SUBENTRY_PRICE_ADJUST, CONF_SOURCE_ENTITY_ID)
		errors: dict[str, str] = {}

		if user_input is not None:
			source = user_input.get(CONF_SOURCE_ENTITY_ID)
			if not source:
				errors[CONF_SOURCE_ENTITY_ID] = "no_sensor_selected"
			elif source in configured:
				errors[CONF_SOURCE_ENTITY_ID] = "already_configured"
			else:
				add_amount = float(user_input.get(CONF_ADD_AMOUNT) or 0)
				name = _clean_name(user_input.get(CONF_NAME))
				data = {
					CONF_PRICE_ID: uuid.uuid4().hex,
					CONF_SOURCE_ENTITY_ID: source,
					CONF_ADD_AMOUNT: add_amount,
				}
				if name:
					data[CONF_NAME] = name
				return self.async_create_entry(
					title=name or price_title(get_friendly_name(self.hass, source), add_amount),
					data=data,
					unique_id=subentry_unique_id(SUBENTRY_PRICE_ADJUST, source),
				)

		return self.async_show_form(
			step_id="user",
			data_schema=vol.Schema({
				vol.Required(CONF_SOURCE_ENTITY_ID): EntitySelector(_price_picker_config(self.hass, configured)),
				vol.Required(CONF_ADD_AMOUNT, default=0.0): ADD_AMOUNT_SELECTOR,
				vol.Optional(CONF_NAME): NAME_SELECTOR,
			}),
			errors=errors,
		)

	async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
		entry = self._entry()
		subentry = self._get_reconfigure_subentry()
		source = str(subentry.data.get(CONF_SOURCE_ENTITY_ID) or "")

		if user_input is not None:
			add_amount = float(user_input.get(CONF_ADD_AMOUNT) or 0)
			name = _clean_name(user_input.get(CONF_NAME))
			return self.async_update_and_abort(
				entry,
				subentry,
				title=name or price_title(get_friendly_name(self.hass, source), add_amount),
				data_updates={CONF_ADD_AMOUNT: add_amount, CONF_NAME: name},
			)

		return self.async_show_form(
			step_id="reconfigure",
			data_schema=vol.Schema({
				vol.Required(CONF_ADD_AMOUNT, default=float(subentry.data.get(CONF_ADD_AMOUNT) or 0)): ADD_AMOUNT_SELECTOR,
				vol.Optional(CONF_NAME, default=str(subentry.data.get(CONF_NAME) or "")): NAME_SELECTOR,
			}),
			description_placeholders={"source": source},
		)


class SettingsOptionsFlow(OptionsFlow):
	"""Global calculation and diagnostic settings (one form)."""

	async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
		if user_input is not None:
			return self.async_create_entry(title="", data={**self.config_entry.options, **user_input})

		current = {**self.config_entry.data, **self.config_entry.options}
		schema = vol.Schema({
			vol.Optional(
				CONF_SAMPLE_INTERVAL, default=current.get(CONF_SAMPLE_INTERVAL, DEFAULT_SAMPLE_INTERVAL)
			): NumberSelector(
				NumberSelectorConfig(min=5, max=300, step=5, unit_of_measurement="s", mode=NumberSelectorMode.SLIDER)
			),
			vol.Optional(CONF_USE_STATISTICAL, default=current.get(CONF_USE_STATISTICAL, True)): BooleanSelector(),
			vol.Optional(
				CONF_FORCE_STATISTICAL_ONLY, default=current.get(CONF_FORCE_STATISTICAL_ONLY, False)
			): BooleanSelector(),
			vol.Optional(
				CONF_STAT_LOOKBACK_MINUTES,
				default=current.get(CONF_STAT_LOOKBACK_MINUTES, DEFAULT_STAT_LOOKBACK_MINUTES),
			): NumberSelector(
				NumberSelectorConfig(min=5, max=120, step=5, unit_of_measurement="min", mode=NumberSelectorMode.SLIDER)
			),
			vol.Optional(CONF_MAX_ENERGY_PER_HOUR, default=current.get(CONF_MAX_ENERGY_PER_HOUR, 0)): NumberSelector(
				NumberSelectorConfig(min=0, max=100, step=0.5, unit_of_measurement="kWh/h", mode=NumberSelectorMode.BOX)
			),
			vol.Optional(
				CONF_CREATE_SYNTHETIC_GRID_TOTAL, default=current.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False)
			): BooleanSelector(),
			vol.Optional(CONF_DEBUG_LOGGING, default=current.get(CONF_DEBUG_LOGGING, False)): BooleanSelector(),
		})
		return self.async_show_form(step_id="init", data_schema=schema)
