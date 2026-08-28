"""Options flow for Energy Sensor Generator."""
from __future__ import annotations

import logging
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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
	CONF_CONSTANT_POWER_DEVICES,
	CONF_CREATE_SYNTHETIC_GRID_TOTAL,
	CONF_DEBUG_LOGGING,
	CONF_FORCE_STATISTICAL_ONLY,
	CONF_MAX_ENERGY_PER_HOUR,
	CONF_PRICE_ADJUST_SENSORS,
	CONF_STAT_LOOKBACK_MINUTES,
	CONF_USE_STATISTICAL,
)
from .sensor_picker import (
	grouped_selector_options,
	merge_saved_options,
	options_overview,
	period_flags_from_selection,
	period_selection_from_flags,
	short_sensor_label,
	uniquify_labels,
)
from .utils import format_constant_power_devices_text

_LOGGER = logging.getLogger(__name__)

PERIOD_OPTIONS = [
	{"value": "daily", "label": "Daily"},
	{"value": "weekly", "label": "Weekly"},
	{"value": "monthly", "label": "Monthly"},
	{"value": "annual", "label": "Annual"},
]


class EnergySensorGeneratorOptionsFlow(config_entries.OptionsFlow):
	"""Menu-based options flow: one concern per step, saved together at the end."""

	def __init__(self) -> None:
		self._errors: dict = {}
		self._user_defaults: dict = {}

	def _defaults(self) -> dict:
		return {**self.config_entry.data, **self.config_entry.options, **self._user_defaults}

	def _get_constant_devices(self) -> list:
		return [dict(device) for device in self._defaults().get(CONF_CONSTANT_POWER_DEVICES, []) or []]

	def _set_constant_devices(self, devices: list) -> None:
		self._user_defaults[CONF_CONSTANT_POWER_DEVICES] = [dict(device) for device in devices]

	def _get_price_adjustments(self) -> list:
		return [dict(item) for item in self._defaults().get(CONF_PRICE_ADJUST_SENSORS, []) or []]

	def _set_price_adjustments(self, items: list) -> None:
		self._user_defaults[CONF_PRICE_ADJUST_SENSORS] = [dict(item) for item in items]

	def _build_options(self, extra: dict | None = None) -> dict:
		"""Merge saved options with in-progress edits without resetting advanced keys."""
		return merge_saved_options(
			self.config_entry.data,
			self.config_entry.options,
			self._user_defaults,
			extra or {},
		)

	def _overview_placeholders(self) -> dict[str, str]:
		defaults = self._defaults()
		found = self._discovered_power_sensors()
		selected = defaults.get("selected_power_sensors", []) or []
		selected_valid = [entity_id for entity_id in selected if entity_id in found or self.hass.states.get(entity_id)]
		return {
			"overview": options_overview(
				selected_count=len(selected_valid),
				found_count=len(found),
				constant_count=len(self._get_constant_devices()),
				price_count=len(self._get_price_adjustments()),
			)
		}

	def _discovered_power_sensors(self) -> list[str]:
		from .__init__ import detect_power_sensors
		return detect_power_sensors(self.hass)

	def _sensor_choice_items(self, entity_ids: list[str]) -> list[tuple[str, str, str | None]]:
		"""(entity_id, short label, device name) for the grouped checkbox list."""
		entity_registry = er.async_get(self.hass)
		device_registry = dr.async_get(self.hass)
		labels: dict[str, str] = {}
		devices: dict[str, str | None] = {}

		for entity_id in entity_ids:
			entity = entity_registry.async_get(entity_id)
			state = self.hass.states.get(entity_id)
			friendly_name = None
			if state and state.attributes.get("friendly_name"):
				friendly_name = state.attributes["friendly_name"]
			elif entity and (entity.name or entity.original_name):
				friendly_name = entity.name or entity.original_name

			device_name = None
			if entity and entity.device_id:
				device = device_registry.async_get(entity.device_id)
				if device:
					device_name = device.name_by_user or device.name

			devices[entity_id] = device_name
			labels[entity_id] = short_sensor_label(entity_id, friendly_name, device_name)

		labels = uniquify_labels(labels)
		return [(entity_id, labels[entity_id], devices[entity_id]) for entity_id in entity_ids]

	def _available_power_sensors(self) -> list[str]:
		"""Auto-detected sensors plus any previously selected entities that still exist."""
		discovered = list(self._discovered_power_sensors())
		seen = set(discovered)
		for entity_id in self._defaults().get("selected_power_sensors", []) or []:
			if entity_id in seen:
				continue
			if self.hass.states.get(entity_id) is not None:
				discovered.append(entity_id)
				seen.add(entity_id)
		return discovered

	async def async_step_init(self, user_input=None):
		"""Landing menu with a one-line summary instead of a wall of text."""
		self._errors = {}
		return self.async_show_menu(
			step_id="init",
			menu_options=[
				"sensors",
				"constant_devices",
				"price_adjustments",
				"advanced",
				"save",
			],
			description_placeholders=self._overview_placeholders(),
		)

	async def async_step_sensors(self, user_input=None):
		"""Pick power sensors as a grouped checkbox list, plus period sensors."""
		self._errors = {}
		available = self._available_power_sensors()
		defaults = self._defaults()
		current_selected = [
			entity_id for entity_id in (defaults.get("selected_power_sensors", []) or [])
			if entity_id in available
		]

		if user_input is not None:
			selected = list(user_input.get("selected_power_sensors", []) or [])
			custom_sensor = user_input.get("custom_power_sensor") or ""
			if custom_sensor and custom_sensor not in selected:
				selected.append(custom_sensor)
			self._user_defaults["selected_power_sensors"] = selected
			self._user_defaults.update(period_flags_from_selection(user_input.get("period_sensors")))
			return await self.async_step_init()

		selector_options = grouped_selector_options(self._sensor_choice_items(available))
		schema = {
			vol.Optional("selected_power_sensors", default=current_selected): SelectSelector(
				SelectSelectorConfig(
					options=selector_options,
					multiple=True,
					mode=SelectSelectorMode.LIST,
				)
			),
			vol.Optional("custom_power_sensor"): EntitySelector(
				EntitySelectorConfig(domain="sensor", multiple=False)
			),
			vol.Optional(
				"period_sensors",
				default=period_selection_from_flags(defaults),
			): SelectSelector(
				SelectSelectorConfig(
					options=PERIOD_OPTIONS,
					multiple=True,
					mode=SelectSelectorMode.LIST,
				)
			),
		}
		return self.async_show_form(
			step_id="sensors",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"count": str(len(available)),
			},
		)

	async def async_step_advanced(self, user_input=None):
		"""Advanced calculation and diagnostic settings."""
		self._errors = {}
		defaults = self._defaults()
		if user_input is not None:
			self._user_defaults.update(user_input)
			return await self.async_step_init()

		schema = {
			vol.Optional(
				"sample_interval",
				default=defaults.get("sample_interval", 60),
			): NumberSelector(
				NumberSelectorConfig(
					min=5,
					max=300,
					step=5,
					unit_of_measurement="seconds",
					mode=NumberSelectorMode.SLIDER,
				)
			),
			vol.Optional(
				CONF_DEBUG_LOGGING,
				default=defaults.get(CONF_DEBUG_LOGGING, False),
			): BooleanSelector(),
			vol.Optional(
				CONF_USE_STATISTICAL,
				default=defaults.get(CONF_USE_STATISTICAL, True),
			): BooleanSelector(),
			vol.Optional(
				CONF_FORCE_STATISTICAL_ONLY,
				default=defaults.get(CONF_FORCE_STATISTICAL_ONLY, False),
			): BooleanSelector(),
			vol.Optional(
				CONF_STAT_LOOKBACK_MINUTES,
				default=defaults.get(CONF_STAT_LOOKBACK_MINUTES, 30),
			): NumberSelector(
				NumberSelectorConfig(
					min=5,
					max=120,
					step=5,
					unit_of_measurement="minutes",
					mode=NumberSelectorMode.SLIDER,
				)
			),
			vol.Optional(
				CONF_MAX_ENERGY_PER_HOUR,
				default=defaults.get(CONF_MAX_ENERGY_PER_HOUR, 0),
			): NumberSelector(
				NumberSelectorConfig(
					min=0,
					max=100.0,
					step=0.5,
					unit_of_measurement="kWh/hour (0=disabled)",
					mode=NumberSelectorMode.BOX,
				)
			),
			vol.Optional(
				CONF_CREATE_SYNTHETIC_GRID_TOTAL,
				default=defaults.get(CONF_CREATE_SYNTHETIC_GRID_TOTAL, False),
			): BooleanSelector(),
		}
		return self.async_show_form(
			step_id="advanced",
			data_schema=vol.Schema(schema),
			errors=self._errors,
		)

	async def async_step_save(self, user_input=None):
		"""Persist the merged options and generate sensors."""
		result = self.async_create_entry(title="Power Sensors", data=self._build_options())
		self.hass.async_create_task(self._async_generate_sensors_after_config())
		return result

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
			summary_lines.append(f"{name} — add {add_amount}")
		price_adjust_summary = "\n".join(summary_lines) or "None configured"

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
				return await self.async_step_init()

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
						existing = next((item for item in items if item.get("source_entity_id") == source_entity), None)
						config_id = str(existing["id"]) if existing and existing.get("id") else uuid.uuid4().hex
						items = [
							item for item in items
							if item.get("id") != config_id and item.get("source_entity_id") != source_entity
						]
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
					items = [item for item in items if str(item.get("id")) != str(target_id)]
					self._set_price_adjustments(items)
					self._user_defaults["_price_adjust_status"] = "Removed entry"
					return await self.async_step_price_adjustments()

			if action == "clear":
				if items:
					self._set_price_adjustments([])
					self._user_defaults["_price_adjust_status"] = "Cleared all entries"
					return await self.async_step_price_adjustments()
				self._errors["base"] = "no_price_adjustments"

		schema = {
			vol.Optional("price_adjust_action", default="add"): SelectSelector(
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
			),
			vol.Optional("price_adjust_source"): EntitySelector(
				EntitySelectorConfig(domain="sensor", multiple=False)
			),
			vol.Optional("price_adjust_add_amount", default=0.0): NumberSelector(
				NumberSelectorConfig(
					min=-10.0,
					max=10.0,
					step=0.001,
					unit_of_measurement="(same as source)",
					mode=NumberSelectorMode.BOX,
				)
			),
			vol.Optional("price_adjust_name"): TextSelector(TextSelectorConfig(multiline=False)),
		}
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

	async def async_step_constant_devices(self, user_input=None):
		"""Manage constant power device definitions."""
		self._errors = {}
		devices = self._get_constant_devices()
		status_message = self._user_defaults.pop("_constant_devices_status", None)
		constant_summary = format_constant_power_devices_text(devices) or "None configured"

		remove_options = []
		for device in devices:
			switch_id = device.get("switch_entity_id")
			if not switch_id:
				continue
			power_w = device.get("power_w")
			try:
				power_display = f"{float(power_w):g}"
			except (TypeError, ValueError):
				power_display = str(power_w)
			remove_options.append({
				"value": switch_id,
				"label": f"{device.get('name') or switch_id} — {power_display} W",
			})

		if user_input is not None:
			action = user_input.get("constant_device_action", "finish")

			if action == "finish":
				return await self.async_step_init()

			if action == "add":
				switch_entity = user_input.get("constant_device_switch")
				power_value = user_input.get("constant_device_power")
				friendly_name = user_input.get("constant_device_name")

				if not switch_entity or power_value is None:
					self._errors["base"] = "constant_device_missing_fields"
				else:
					try:
						power_w = float(power_value)
					except (TypeError, ValueError):
						self._errors["base"] = "constant_device_invalid_power"
					else:
						if power_w <= 0:
							self._errors["base"] = "constant_device_invalid_power"
						else:
							devices = [device for device in devices if device.get("switch_entity_id") != switch_entity]
							entry = {"switch_entity_id": switch_entity, "power_w": power_w}
							if friendly_name:
								entry["name"] = friendly_name
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
					devices = [device for device in devices if device.get("switch_entity_id") != target_switch]
					self._set_constant_devices(devices)
					self._user_defaults["_constant_devices_status"] = f"Removed {target_switch}"
					return await self.async_step_constant_devices()

			elif action == "clear":
				if devices:
					self._set_constant_devices([])
					self._user_defaults["_constant_devices_status"] = "Cleared all entries"
					return await self.async_step_constant_devices()
				self._errors["base"] = "no_constant_devices"
			else:
				self._errors["base"] = "constant_device_unknown_action"

		schema = {
			vol.Optional("constant_device_action", default="add"): SelectSelector(
				SelectSelectorConfig(
					options=[
						{"value": "add", "label": "Add / update device"},
						{"value": "remove", "label": "Remove device"},
						{"value": "clear", "label": "Remove all"},
						{"value": "finish", "label": "Done"},
					],
					multiple=False,
					mode=SelectSelectorMode.DROPDOWN,
				)
			),
			vol.Optional("constant_device_switch"): EntitySelector(
				EntitySelectorConfig(domain=["switch", "input_boolean"], multiple=False)
			),
			vol.Optional("constant_device_power", default=3000): NumberSelector(
				NumberSelectorConfig(
					min=1,
					max=20000,
					step=10,
					unit_of_measurement="Watts",
					mode=NumberSelectorMode.BOX,
				)
			),
			vol.Optional("constant_device_name"): TextSelector(TextSelectorConfig(multiline=False)),
		}
		if remove_options:
			schema[vol.Optional("constant_device_remove")] = SelectSelector(
				SelectSelectorConfig(
					options=remove_options,
					multiple=False,
					mode=SelectSelectorMode.DROPDOWN,
				)
			)

		return self.async_show_form(
			step_id="constant_devices",
			data_schema=vol.Schema(schema),
			errors=self._errors,
			description_placeholders={
				"constant_summary": constant_summary,
				"constant_status": status_message or "",
			},
		)

	async def _async_generate_sensors_after_config(self):
		"""Generate sensors automatically after configuration is saved."""
		import asyncio

		await asyncio.sleep(2)
		try:
			from .__init__ import generate_sensors_service

			_LOGGER.info("Auto-generating energy sensors after configuration update...")
			await generate_sensors_service(self.hass, None, self.config_entry)
			_LOGGER.info("Energy sensors generated successfully after configuration update")
			await self.hass.services.async_call(
				"persistent_notification",
				"create",
				{
					"message": "Energy sensors have been created automatically based on your new configuration. Check the Entities page to see your new sensors.",
					"title": "Energy Sensor Generator",
					"notification_id": "energy_sensor_generator_created",
				},
				blocking=False,
			)
		except Exception as err:
			_LOGGER.error("Failed to auto-generate sensors after configuration: %s", err)
			await self.hass.services.async_call(
				"persistent_notification",
				"create",
				{
					"message": f"Failed to automatically create energy sensors: {err}. You may need to manually reload the integration.",
					"title": "Energy Sensor Generator - Error",
					"notification_id": "energy_sensor_generator_error",
				},
				blocking=False,
			)
