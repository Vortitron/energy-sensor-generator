# Changelog

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
