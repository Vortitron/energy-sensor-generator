# Energy Sensor Generator

A Home Assistant custom integration that builds kWh energy sensors from power sensors (W / kW), entirely in Python. It does not create YAML `integration` or `utility_meter` helpers.

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Vortitron&repository=energy-sensor-generator&category=integration)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

## What it does

- Finds power sensors (`device_class: power`, or unit `W` / `kW`).
- Integrates power over time into `total_increasing` kWh sensors for the Energy dashboard.
- Optionally adds daily, weekly, monthly and annual totals.
- Can model fixed loads from a switch plus a rated wattage (no inline meter).
- Can wrap a price sensor (for example Nordpool) and add a fixed transmission/tax amount.

Generated energy sensors are attached to the *source* device, not to a separate Energy Sensor Generator device.

## Installation

### HACS (recommended)

Until this repository is in the HACS default store, add it as a custom repository:

1. HACS → Integrations → ⋮ → Custom repositories.
2. URL: `https://github.com/Vortitron/energy-sensor-generator`, category: **Integration**.
3. Download **Energy Sensor Generator**, then restart Home Assistant.
4. Settings → Devices & Services → Add Integration → Energy Sensor Generator.

Or use the badge above.

### Manual

Copy `custom_components/energy_sensor_generator` into your Home Assistant `config/custom_components/` folder and restart.

**Requirements:** Home Assistant 2025.3 or later. No extra Python packages.

## Configuration

Setup asks one question: which power sensors to track. The picker is searchable and only lists sensors that report power (W / kW).

After that, everything lives on the integration page (Settings → Devices & services → Energy Sensor Generator):

| Button | What it adds |
| --- | --- |
| **Add energy sensor** | A kWh sensor for one power sensor, with optional daily / weekly / monthly / annual totals. Only untracked power sensors are offered. Optionally name the meter's own kWh counter to fill gaps (see below). |
| **Add constant load** | A switch or `input_boolean` plus a rated wattage, for devices without a power meter. |
| **Add price add-on** | Mirrors a price sensor (for example Nordpool) and adds a fixed amount. Sensors priced per kWh are offered first. |

Each source is listed on the page with its own entities. Use its gear icon to rename it or change its period totals, or its menu to delete it. Deleting a source removes its entities; its stored total is kept and comes back if you add the same source again.

The integration's own gear icon opens **Settings**: sampling interval, recorder-history calculation, spike cap, a synthetic grid total, and debug logging.

### Upgrading from 0.0.x

The first start after upgrading moves your existing selections into this layout automatically. Unique IDs and storage keys do not change, so totals and Energy dashboard history are kept. The old global period setting is applied to every migrated source.

Then add the generated `*_energy` / `*_daily_energy` sensors under **Settings → Dashboards → Energy**.

## How energy is calculated

Power is converted to kW (`W ÷ 1000`, or already kW), then integrated over time:

1. **Statistical (default)** — left Riemann sum over recorder history, matching Home Assistant's integration helper, including the final slice of each window. Windows tile, so consecutive intervals neither overlap nor leave a gap.
2. **Point sampling (fallback)** — the same left Riemann rule from live state changes, plus the held-power tail up to the interval tick. If a source has been offline or Home Assistant was restarting for longer than `max(10 minutes, 3 × sample interval)`, that gap is skipped rather than filled with guessed energy.

**Filling gaps from the meter's counter.** If the power sensor comes from a meter that also reports a cumulative kWh total (for example a P1 / HAN reader), choose that total as the source's *Meter energy counter*. A gap is then filled with the counter's rise across it instead of being skipped. Sources that share one counter, such as a meter's three phases, split it in proportion to their power just before and after the gap. The fill is added once the counter has a reading after the gap, so on the Energy dashboard it appears in that hour rather than spread over the gap.

Constant-power devices use the switch state (`on` / `open` → rated watts, otherwise 0) and point sampling only.

## Glitches and outages

- **Implausible readings:** a power reading above the limit (50 kW by default, under Settings; or a per-source limit in the energy sensor's Edit dialog) is ignored and the last plausible reading is held. Ignored readings are counted in the `rejected_readings` attribute.
- **Recorder stalls:** when history lags behind the live sensor, the window is calculated from live readings.
- **Restarts and offline sources:** gaps longer than `max(10 minutes, 3 × sample interval)` are skipped, not bridged. If the source has a meter energy counter configured, the gap is filled from that counter.
- **No backwards steps:** on start-up the total continues from the higher of the stored value and the last recorded state.

## Services

Useful calls from Developer Tools → Services:

- `energy_sensor_generator.generate_sensors` — rebuild the sensors from the saved configuration (same as reloading).
- `energy_sensor_generator.debug_sensor_detection` — log which power sensors were detected.
- `energy_sensor_generator.copy_from_previous_hour` — copy a known-good hour if an hour looks wrong after a long restart.
- `energy_sensor_generator.reset_energy_sensors` — scale or zero stored totals if you need a fresh start.
- `energy_sensor_generator.adjust_energy` — add to, set, or copy one sensor's total (e.g. to remove a spike).
- `energy_sensor_generator.export_energy_data` / `import_energy_data` — back up and restore stored totals. Files are kept in the `energy_sensor_generator_backups` folder of the config directory; give a plain file name such as `energy_backup.json`.

Services that change or export stored totals (export, import, reset, adjust, copy from previous hour, reset statistical tracking, entity ID migration) are for administrators only. Automations and scripts can still call them.

See `custom_components/energy_sensor_generator/services.yaml` for the full list.

## Troubleshooting

- **No power sensors listed:** the entity must have unit `W`/`kW` or `device_class: power`. Use the debug detection service.
- **Negative bars on the Energy dashboard:** the generated sensors only increase. Negative “untracked” usually means a child device is nested under the wrong parent in the Energy dashboard (`included_in_stat`).
- **Values look low/high:** enable debug logging in Settings, then check Logs for `energy_sensor_generator`.

## HACS default listing

This repository is set up for HACS:

- `hacs.json` in the repo root
- `issue_tracker` in `manifest.json`
- Brand icon in `custom_components/energy_sensor_generator/brand/icon.png`
- Hassfest and HACS GitHub Actions in `.github/workflows/validate.yml`

After CI is green on a tagged GitHub **release**, the remaining listing steps (repo description, topics, then a PR to [hacs/default](https://github.com/hacs/default)) are documented in `project_outline.md`.

## License

MIT. See [LICENSE](LICENSE).

Developed by [Vortitron](https://github.com/Vortitron).
