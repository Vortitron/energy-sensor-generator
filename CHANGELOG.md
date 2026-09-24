# Changelog

## 0.0.87

Bug sweep and UX polish.

**Fixes**

- **Power sensors page crashed** for most setups: the device-grouped list used nested option groups, which Home Assistant's selector rejects. Grouping is now shown in the label (`Meter › L1`).
- **Services did nothing:** most services were registered as lambdas, which Home Assistant runs in a thread without awaiting. All services now run, validate their input, and show errors in the UI.
- **Adjust / reset / import / copy-from-hour were undone** on the sensor's next save, because they edited storage behind the live entity. They now update the entity directly.
- **Sampling interval setting was ignored** (always 60 s).
- **Period sensors stayed at 0** when the main sensor's entity ID came from its friendly name, or after it was renamed. They now find the main sensor by unique ID.
- **Missed period resets:** if Home Assistant was off at midnight, the daily/weekly/monthly/annual total kept the previous period's energy. The reset now catches up on start.
- **Synthetic grid total spikes:** a source that was briefly unavailable (e.g. on restart) made the total dip and jump back, which the Energy dashboard records as a reset plus a spike. Sensors named `*_power_<n>` were also left out of the total.
- **`*_power_<n>` sensors** could lose their main energy entity on restart and have it recreated under a different ID.
- **Data loss on restart/reload:** up to a minute of pending writes could be lost at shutdown, and reloads could wait up to a minute. A fresh install could also drop early writes.
- **Unavailable sensors were dropped** (and their energy sensors deleted) when saving the Power sensors page while the source was offline. They are now listed as "(unavailable)" and kept.
- Device actions now pass their fields (e.g. the sensor name for Diagnose).
- Power sensor detection no longer picks up `%`, data, or water "usage" sensors by name.
- Minimum Home Assistant version corrected to 2024.11 (the Configure dialog needs it).

**Behaviour changes**

- Saving the Configure dialog reloads the integration instead of running a separate generate step, and no longer posts a "sensors created" notification.
- With no power sensors ticked, no power-based sensors are created. Previously every detected power sensor got one, and those were orphaned after the next restart.
- `generate_sensors` now reloads the integration.
- On the constant device and price add-on pages, submitting without picking an entity returns to the menu. The menu shows when there are unsaved changes.

## 0.0.86

- **Options UI:** Configure is now a short menu (power sensors, constant loads, price add-ons, advanced, save) instead of one form with a wall of help text.
- **Sensor list:** Power sensors are grouped by device as a checkbox list with compact labels, so dozens of devices stay scannable instead of overflowing chips.
- **Options save:** Submitting the main form no longer resets advanced settings (lookback, spike cap, statistical flags) back to defaults.
- **Point sampling:** Energy for an interval is `pending segments + held-power tail` from the current last-update anchor, so a state change during a history lookup cannot double-count the window.
- **HACS packaging:** Added `hacs.json`, `issue_tracker`, brand icon, hassfest/HACS GitHub Actions, and `strings.json`. Minimum Home Assistant version is 2024.4.

## 0.0.85

- Removed the post-restart audit and its persistent notification. The audit could roll back legitimate energy, and the rollback itself was recorded as a negative delta in long-term statistics.
- Gap guard for restarts/offline sources: if the time since the last calculation anchor exceeds `max(sample_interval × 3, 10 minutes)`, the window restarts from now.
- Fixed systematic under-read (~17%) in statistical calculation: each window now includes the final segment up to the window end (left Riemann sum, matching Home Assistant's integration helper).
- Point sampling no longer loses energy between ticks: state changes accumulate into a pending bucket.
- Calculation anchors are persisted on every interval; storage writes are atomic via a shared lock.
- Pure energy maths extracted to `energy_math.py`; period sensors share `PeriodEnergySensor`; stale generated devices can be deleted from the UI.

## 0.0.47

- Statistical calculation uses `recorder.get_instance(hass).async_add_executor_job()` so history access is async and non-blocking.

## 0.0.34

- kW power sensors are detected and converted with factor 1 (not divided by 1000 again).

## 0.0.23

- Energy calculations run on interval timers only, to stop double-counting from overlapping state-change and interval paths.

Earlier versions are in the git history.
