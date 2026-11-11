# Energy Sensor Generator

A Home Assistant custom integration that turns power sensors (W / kW) into kWh energy sensors, and adds what Home Assistant's own helpers leave out: period totals, constant loads, price add-ons and, where a meter misbehaves, a protected calculation.

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Vortitron&repository=energy-sensor-generator&category=integration)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

## Built-in or Energy Sensor Generator?

Since 2026.11, Home Assistant's Energy dashboard can turn a power sensor into energy by itself: add a device with only a power sensor and it offers to create an Integral helper. If that is all you need, you don't need this integration.

This integration builds on the same Integral helpers, so the two work together. For each power sensor it reuses the helper the Energy dashboard already created, or creates one with the same settings. The dashboard then recognises it in turn. On top of that it adds:

| | Energy dashboard alone | With Energy Sensor Generator |
| --- | --- | --- |
| kWh from a power sensor | Integral helper | The same Integral helper (reused, or created for you) |
| Daily / weekly / monthly / annual totals | One `utility_meter` helper per period, by hand | Ticked per source |
| Devices without a meter (a switch and a wattage) | Not covered | Constant load: a power sensor plus its Integral helper |
| Price plus fixed fees (e.g. Nordpool + transmission) | Template sensor | Price add-on |
| Many sources at once | One at a time | One dialog |
| Glitch readings (one bad 183 kW value) | Integrated as real energy | Ignored by the Protected calculation |
| Gaps when the source or Home Assistant is offline | Skipped | Protected: filled from the meter's own kWh counter |
| Total after a lost save | Can step back | Protected: never goes backwards |

## What it does

- Finds power sensors (`device_class: power`, or unit `W` / `kW`).
- Gives each one a kWh total for the Energy dashboard: a core Integral helper (**Built-in**, the default), or this integration's own calculation (**Protected**).
- Optionally adds daily, weekly, monthly and annual totals.
- Can model fixed loads from a switch plus a rated wattage (no inline meter).
- Can wrap a price sensor (for example Nordpool) and add a fixed transmission/tax amount.

Generated sensors are grouped on a device per source, under the integration's page.

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
| **Add energy sensor** | A kWh total for one power sensor, with optional daily / weekly / monthly / annual totals. Only untracked power sensors are offered. Choose **Built-in** or **Protected** (see below). |
| **Add constant load** | A switch or `input_boolean` plus a rated wattage, for devices without a power meter. |
| **Add price add-on** | Mirrors a price sensor (for example Nordpool) and adds a fixed amount. Sensors priced per kWh are offered first. |

Each source is listed on the page with its own entities. Use its gear icon to rename it or change its period totals, or its menu to delete it. Deleting a source removes its entities; its stored total is kept and comes back if you add the same source again.

The integration's own gear icon opens **Settings**: sampling interval, recorder-history calculation, spike cap, a synthetic grid total, and debug logging. Apart from the grid total and logging, these only affect Protected sources.

### Built-in or Protected

**Built-in** (the default for new sources) uses a Home Assistant Integral helper for the kWh total, with the settings the Energy dashboard uses (left Riemann sum, 5-minute maximum sub-interval, kWh). If one already integrates that power sensor, for example one the Energy dashboard created, it is reused rather than duplicated. Otherwise one is created and named after the source, e.g. *Kettle energy*. Use that helper on the Energy dashboard; the period totals follow it.

A helper this integration created is removed when you delete the source, unless the Energy dashboard uses it. A helper it reused is never removed.

A built-in **constant load** publishes a power sensor (rated watts while the switch is on, 0 when off), and an Integral helper turns that into kWh. The Energy dashboard can then show the load's live power as well.

**Protected** uses this integration's own calculation (below). Choose it for a power sensor that sometimes reports impossible values, or a meter that drops out, where you want the spike cap, gap filling from the meter's counter, and a total that never goes backwards. The Energy dashboard does not recognise these sensors as its own, so pick the generated sensor yourself when adding the device.

Sources created before 0.2.0 keep the Protected calculation and their history.

### Upgrading from 0.0.x

The first start after upgrading moves your existing selections into this layout automatically. Unique IDs and storage keys do not change, so totals and Energy dashboard history are kept. The old global period setting is applied to every migrated source.

Then add the generated `*_energy` / `*_daily_energy` sensors under **Settings → Dashboards → Energy**.

## How the Protected calculation works

Power is converted to kW (`W ÷ 1000`, or already kW), then integrated over time:

1. **Statistical (default)** — left Riemann sum over recorder history, matching Home Assistant's integration helper, including the final slice of each window. Windows tile, so consecutive intervals neither overlap nor leave a gap.
2. **Point sampling (fallback)** — the same left Riemann rule from live state changes, plus the held-power tail up to the interval tick. If a source has been offline or Home Assistant was restarting for longer than `max(10 minutes, 3 × sample interval)`, that gap is skipped rather than filled with guessed energy.

**Filling gaps from the meter's counter.** If the power sensor comes from a meter that also reports a cumulative kWh total (for example a P1 / HAN reader), choose that total as the source's *Meter energy counter*. A gap is then filled with the counter's rise across it instead of being skipped. Sources that share one counter, such as a meter's three phases, split it in proportion to their power just before and after the gap. The fill is added once the counter has a reading after the gap, so on the Energy dashboard it appears in that hour rather than spread over the gap.

Constant-power devices use the switch state (`on` / `open` → rated watts, otherwise 0) and point sampling only.

## Glitches and outages (Protected)

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
