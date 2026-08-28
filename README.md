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

**Requirements:** Home Assistant 2024.4 or later. No extra Python packages.

## Configuration

After adding the integration, open **Configure**. The options dialog is a short menu, not one long form:

| Section | Purpose |
| --- | --- |
| **Power sensors** | Checkbox list grouped by device. Tick the power entities that should get kWh sensors. Period totals (daily / weekly / monthly / annual) are on the same page. |
| **Constant power devices** | Pair a switch or `input_boolean` with a rated wattage. |
| **Electricity price add-ons** | Source price sensor + fixed adder. |
| **Advanced** | Sampling interval, statistical calculation, debug logging, spike cap, synthetic grid total. |
| **Save** | Writes the options and generates sensors. |

The menu header is a one-line summary, for example: `12 of 64 power sensors selected · 3 constant loads · 1 price add-on`.

Changes in each section are kept until you choose **Save**. Closing the dialog without saving discards in-progress edits.

Then add the generated `*_energy` / `*_daily_energy` sensors under **Settings → Dashboards → Energy**.

## How energy is calculated

Power is converted to kW (`W ÷ 1000`, or already kW), then integrated over time:

1. **Statistical (default)** — left Riemann sum over recorder history, matching Home Assistant's integration helper, including the final slice of each window. Windows tile, so consecutive intervals neither overlap nor leave a gap.
2. **Point sampling (fallback)** — the same left Riemann rule from live state changes, plus the held-power tail up to the interval tick. If a source has been offline or Home Assistant was restarting for longer than `max(10 minutes, 3 × sample interval)`, that gap is skipped rather than filled with guessed energy.

Constant-power devices use the switch state (`on` / `open` → rated watts, otherwise 0) and point sampling only.

## Services

Useful calls from Developer Tools → Services:

- `energy_sensor_generator.generate_sensors` — create or refresh sensors after a config change.
- `energy_sensor_generator.debug_sensor_detection` — log which power sensors were detected.
- `energy_sensor_generator.copy_from_previous_hour` — copy a known-good hour if an hour looks wrong after a long restart.
- `energy_sensor_generator.reset_energy_sensors` — scale or zero stored totals if you need a fresh start.

See `custom_components/energy_sensor_generator/services.yaml` for the full list.

## Troubleshooting

- **No power sensors listed:** the entity must have unit `W`/`kW` or `device_class: power`. Use the debug detection service.
- **Device already has energy sensors:** auto-generation skips those devices, but an explicit tick in **Power sensors** still creates kWh sensors.
- **Negative bars on the Energy dashboard:** the generated sensors only increase. Negative “untracked” usually means a child device is nested under the wrong parent in the Energy dashboard (`included_in_stat`).
- **Values look low/high:** enable debug logging under Advanced, reload, then check Logs for `energy_sensor_generator`.

## HACS default listing

This repository is set up for HACS:

- `hacs.json` in the repo root
- `issue_tracker` in `manifest.json`
- Brand icon in `brand/icon.png`
- Hassfest and HACS GitHub Actions in `.github/workflows/validate.yml`

After CI is green on a tagged GitHub **release**, the remaining listing steps (repo description, topics, then a PR to [hacs/default](https://github.com/hacs/default)) are documented in `project_outline.md`.

## License

MIT. See [LICENSE](LICENSE).

Developed by [Vortitron](https://github.com/Vortitron).
