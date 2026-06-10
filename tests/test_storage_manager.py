import asyncio
from unittest.mock import MagicMock

import pytest


class FakeStore:
	def __init__(self, hass, version, key):
		self._hass = hass
		self.version = version
		self.key = key
		self._data = None
		self.saved = []

	async def async_load(self):
		return self._data

	async def async_save(self, data):
		self._data = data
		self.saved.append(data)


@pytest.mark.asyncio
async def test_debounce_and_rate_limit(monkeypatch):
	from custom_components.energy_sensor_generator import utils

	# Patch Store to our fake in the module under test
	fake_store_ref = {"store": None}

	def _fake_store_ctor(hass, version, key):
		fake = FakeStore(hass, version, key)
		fake_store_ref["store"] = fake
		return fake

	monkeypatch.setattr(utils.ha_storage, "Store", _fake_store_ctor)

	# Minimal hass with config.path
	hass = MagicMock()
	hass.config.path = lambda rel: "/tmp/" + str(rel)

	manager = utils.StorageManager(hass, debounce_seconds=0.01, min_interval_seconds=0.05)

	# Issue multiple saves quickly; should coalesce into one write
	await manager.async_save({"v": 1})
	await manager.async_save({"v": 2})
	await asyncio.sleep(0.08)

	store = fake_store_ref["store"]
	assert store is not None
	assert len(store.saved) == 1
	assert store._data == {"v": 2}

	# Next save after interval should trigger a second write
	await manager.async_save({"v": 3})
	await asyncio.sleep(0.06)
	assert len(store.saved) == 2
	assert store._data == {"v": 3}


@pytest.mark.asyncio
async def test_async_update_and_flush(monkeypatch):
	from custom_components.energy_sensor_generator import utils

	# Patch Store
	fake_store_ref = {"store": None}

	def _fake_store_ctor(hass, version, key):
		fake = FakeStore(hass, version, key)
		fake_store_ref["store"] = fake
		return fake

	monkeypatch.setattr(utils.ha_storage, "Store", _fake_store_ctor)

	hass = MagicMock()
	hass.config.path = lambda rel: "/tmp/" + str(rel)

	manager = utils.StorageManager(hass, debounce_seconds=0.005, min_interval_seconds=0.0)

	# async_update should mutate and schedule a save
	def _mut(d: dict):
		d["a"] = 10

	updated = await manager.async_update(_mut)
	assert updated["a"] == 10

	# Flush to ensure write completed
	await manager.async_flush()
	store = fake_store_ref["store"]
	assert len(store.saved) >= 1
	assert store._data["a"] == 10


@pytest.mark.asyncio
async def test_async_set_key_is_atomic(monkeypatch):
	from custom_components.energy_sensor_generator import utils

	fake_store_ref = {"store": None}

	def _fake_store_ctor(hass, version, key):
		fake = FakeStore(hass, version, key)
		fake_store_ref["store"] = fake
		return fake

	monkeypatch.setattr(utils.ha_storage, "Store", _fake_store_ctor)

	hass = MagicMock()
	hass.config.path = lambda rel: "/tmp/" + str(rel)

	manager = utils.StorageManager(hass, debounce_seconds=0.005, min_interval_seconds=0.0)

	# Concurrent single-key writes from different "sensors" must all survive
	await asyncio.gather(
		manager.async_set_key("sensor_a", {"value": 1.0}),
		manager.async_set_key("sensor_b", {"value": 2.0}),
		manager.async_set_key("sensor_c", {"value": 3.0}),
	)
	await manager.async_flush()

	store = fake_store_ref["store"]
	assert store._data["sensor_a"] == {"value": 1.0}
	assert store._data["sensor_b"] == {"value": 2.0}
	assert store._data["sensor_c"] == {"value": 3.0}


