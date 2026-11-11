"""Core Integral helpers that make the kWh total of built-in sources.

Since Home Assistant 2026.11 the Energy dashboard turns a power sensor into
energy by creating an Integral (Riemann sum) helper, and reuses one that
already integrates the same sensor. It only recognises helpers of the
``integration`` platform, so a built-in source here uses exactly such a
helper: an existing one when there is one, or one created with the
dashboard's settings. This integration then adds period totals on top.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from homeassistant.config_entries import SOURCE_USER, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .naming import (
	CALCULATION_BUILTIN,
	SUBENTRY_CONSTANT_LOAD,
	SUBENTRY_POWER_SENSOR,
	SourcePlan,
	constant_power_unique_id,
	integral_flow_input,
	normalise_calculation,
)

_LOGGER = logging.getLogger(__name__)

INTEGRAL_DOMAIN = "integration"

CONF_CALCULATION = "calculation"
CONF_INTEGRAL_ENTRY_ID = "integral_entry_id"
CONF_INTEGRAL_ENTITY_ID = "integral_entity_id"
CONF_INTEGRAL_OWNED = "integral_owned"


def _source_ids(hass: HomeAssistant, entity_id: str) -> set[str]:
	"""An entity ID and its registry ID; helpers may store either as their source."""
	ids = {entity_id}
	if reg_entry := er.async_get(hass).async_get(entity_id):
		ids.add(reg_entry.id)
	return ids


def integral_entity_id(hass: HomeAssistant, integral_entry_id: str) -> str | None:
	"""The sensor an Integral helper's config entry provides, by its current entity ID."""
	for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), integral_entry_id):
		if reg_entry.domain == "sensor":
			return reg_entry.entity_id
	return None


def find_integral(hass: HomeAssistant, power_entity_id: str) -> tuple[str | None, str | None] | None:
	"""``(config entry ID, entity ID)`` of a helper that already integrates the power sensor.

	The same helpers the Energy dashboard would reuse: ``integration``
	platform, this source, an energy result. YAML-defined helpers have no
	config entry, so the first value is None for them.
	"""
	ids = _source_ids(hass, power_entity_id)
	for entry in hass.config_entries.async_entries(INTEGRAL_DOMAIN):
		if entry.disabled_by is not None or entry.options.get("source") not in ids:
			continue
		# A power source makes energy unless another device class was forced
		if entry.options.get("device_class") not in (None, "", "energy"):
			continue
		return entry.entry_id, integral_entity_id(hass, entry.entry_id)

	registry = er.async_get(hass)
	for state in hass.states.async_all("sensor"):
		if state.attributes.get("source") not in ids or state.attributes.get("device_class") != "energy":
			continue
		reg_entry = registry.async_get(state.entity_id)
		if reg_entry is not None and reg_entry.platform == INTEGRAL_DOMAIN:
			return reg_entry.config_entry_id, state.entity_id
	return None


def resolve_integral(hass: HomeAssistant, integral_entry_id: str | None, fallback_entity_id: str | None) -> str | None:
	"""Current entity ID of a source's Integral helper (follows renames)."""
	if integral_entry_id and (entity_id := integral_entity_id(hass, integral_entry_id)):
		return entity_id
	return fallback_entity_id or None


def _power_unit(hass: HomeAssistant, entity_id: str) -> str | None:
	if (state := hass.states.get(entity_id)) and state.attributes.get("unit_of_measurement"):
		return str(state.attributes["unit_of_measurement"])
	if reg_entry := er.async_get(hass).async_get(entity_id):
		return reg_entry.unit_of_measurement
	return None


async def async_create_integral(hass: HomeAssistant, name: str, power_entity_id: str) -> str:
	"""Create an Integral helper through its own config flow; return its config entry ID."""
	user_input = integral_flow_input(name, power_entity_id, _power_unit(hass, power_entity_id))
	if user_input is None:
		raise ValueError(f"{power_entity_id} does not report a power unit yet")
	flow = await hass.config_entries.flow.async_init(INTEGRAL_DOMAIN, context={"source": SOURCE_USER})
	flow_id = flow["flow_id"]
	try:
		result = await hass.config_entries.flow.async_configure(flow_id, user_input)
	except Exception:
		_abort_quietly(hass, flow_id)
		raise
	if result["type"] != FlowResultType.CREATE_ENTRY:
		_abort_quietly(hass, flow_id)
		raise ValueError(f"the Integral helper rejected the settings: {result.get('errors')}")
	return result["result"].entry_id


def _abort_quietly(hass: HomeAssistant, flow_id: str) -> None:
	try:
		hass.config_entries.flow.async_abort(flow_id)
	except Exception:  # noqa: BLE001 - already finished
		pass


def power_entity_for(hass: HomeAssistant, plan: SourcePlan) -> str | None:
	"""The power sensor a built-in source's helper integrates."""
	if plan.constant_config is None:
		return plan.source_entity_id
	# A constant load publishes its own power sensor
	return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, constant_power_unique_id(plan.base_name))


def _link_is_valid(hass: HomeAssistant, data: Mapping[str, Any]) -> bool:
	if entry_id := data.get(CONF_INTEGRAL_ENTRY_ID):
		return hass.config_entries.async_get_entry(entry_id) is not None
	if entity_id := data.get(CONF_INTEGRAL_ENTITY_ID):
		return er.async_get(hass).async_get(entity_id) is not None
	return False


async def async_ensure_integrals(hass: HomeAssistant, entry: ConfigEntry, plans: Iterable[SourcePlan]) -> None:
	"""Give every built-in source an Integral helper: reuse one, or create one.

	The helper is recorded in the source's subentry. A helper this
	integration created is marked as owned, so it is removed with the source.
	"""
	entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
	if entry_data is None:
		return
	async with entry_data.setdefault("integral_lock", asyncio.Lock()):
		for plan in plans:
			if not plan.builtin or plan.subentry_id not in entry.subentries:
				continue
			subentry = entry.subentries[plan.subentry_id]
			if _link_is_valid(hass, subentry.data):
				continue
			power_entity_id = power_entity_for(hass, plan)
			if not power_entity_id:
				continue

			owned = False
			found = find_integral(hass, power_entity_id)
			if found:
				integral_entry_id, entity_id = found
				_LOGGER.info("%s: using the existing Integral helper %s", subentry.title, entity_id or integral_entry_id)
			else:
				try:
					integral_entry_id = await async_create_integral(hass, f"{subentry.title} energy", power_entity_id)
				except Exception as err:  # noqa: BLE001 - reported, retried next start
					_LOGGER.warning("%s: could not create an Integral helper for %s: %s", subentry.title, power_entity_id, err)
					continue
				entity_id = integral_entity_id(hass, integral_entry_id)
				owned = True
				_LOGGER.info("%s: created the Integral helper %s", subentry.title, entity_id or integral_entry_id)

			if entry.subentries.get(plan.subentry_id) is not subentry:
				# The source was edited or deleted meanwhile
				continue
			hass.config_entries.async_update_subentry(
				entry,
				subentry,
				data={
					**subentry.data,
					CONF_INTEGRAL_ENTRY_ID: integral_entry_id or "",
					CONF_INTEGRAL_ENTITY_ID: entity_id or "",
					CONF_INTEGRAL_OWNED: owned,
				},
			)


def _mentions(value: Any, wanted: str) -> bool:
	if isinstance(value, str):
		return value == wanted
	if isinstance(value, Mapping):
		return any(_mentions(item, wanted) for item in value.values())
	if isinstance(value, (list, tuple)):
		return any(_mentions(item, wanted) for item in value)
	return False


async def _used_by_energy_dashboard(hass: HomeAssistant, entity_id: str) -> bool:
	try:
		from homeassistant.components.energy.data import async_get_manager

		manager = await async_get_manager(hass)
		return _mentions(manager.data or {}, entity_id)
	except Exception:  # noqa: BLE001 - when in doubt, keep the helper
		return True


async def async_remove_owned_integral(hass: HomeAssistant, subentry_type: str, title: str, data: Mapping[str, Any]) -> None:
	"""Remove the Integral helper a deleted built-in source created.

	A helper the source only reused belongs to the user and is kept, as is
	one the Energy dashboard still uses (removing it would break the dashboard).
	"""
	if subentry_type not in (SUBENTRY_POWER_SENSOR, SUBENTRY_CONSTANT_LOAD):
		return
	if normalise_calculation(data.get(CONF_CALCULATION)) != CALCULATION_BUILTIN or not data.get(CONF_INTEGRAL_OWNED):
		return
	integral_entry_id = data.get(CONF_INTEGRAL_ENTRY_ID)
	if not integral_entry_id or hass.config_entries.async_get_entry(integral_entry_id) is None:
		return
	entity_id = integral_entity_id(hass, integral_entry_id)
	if entity_id and await _used_by_energy_dashboard(hass, entity_id):
		_LOGGER.info("%s: kept its Integral helper %s, which the Energy dashboard uses", title, entity_id)
		return
	await hass.config_entries.async_remove(integral_entry_id)
	_LOGGER.info("%s: removed its Integral helper %s", title, entity_id or integral_entry_id)
