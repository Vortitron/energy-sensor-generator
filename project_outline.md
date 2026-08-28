# Project outline

Energy Sensor Generator is a Home Assistant custom integration (`custom_components/energy_sensor_generator`) that creates `total_increasing` kWh sensors from power sensors, constant-power switches, and optional price add-ons.

## Layout

- `config_flow.py` — first-time setup (sample interval + debug logging).
- `options_flow.py` — menu-based configure UI (sensors, constant loads, price add-ons, advanced, save).
- `sensor_picker.py` — compact labels, device grouping, option merging (no Home Assistant imports).
- `energy_math.py` — left Riemann / held-power / point-sampling maths (unit tested).
- `sensor.py` / `period_sensors.py` — live entities and interval updates.
- `utils.py` — debounced Store persistence.
- `brand/icon.png` — HACS brand asset.
- `hacs.json` — HACS manifest (name, min HA 2024.4, render README).

## Configure UI

Home Assistant config flows cannot host a custom Lovelace card, so the picker is the native Select selector in **list** mode, grouped by device. Help text lives on each field (`data_description`) rather than a paragraph at the top of the dialog.

Edits are held in memory until **Save**. Closing without Save discards them.

## Calculations (checked in 0.0.86)

- Statistical path: `left_riemann_energy` over recorder history, including the last sample → window end, with windows tiling.
- Point sampling: pending left-Riemann segments from state changes plus a held-power tail from the current `last_update` to now.
- Restarts / offline sources: gaps longer than `max(10 minutes, 3 × sample interval)` are not bridged.
- Units: `conversion_factor_from_unit` maps kW → 1 and W (or unknown) → 1000.

## HACS default listing (remaining steps)

Repo-side files are in place. After pushing 0.0.86 and CI is green:

1. GitHub repo description and topics (`home-assistant`, `hacs`, `energy`, `integration`, `custom-component`).
2. Publish a GitHub **release** (not just a tag) for `0.0.86` once hassfest and HACS actions pass.
3. Confirm the repo is public, issues enabled, and not archived.
4. Open a PR against [hacs/default](https://github.com/hacs/default) adding `vortitron/energy-sensor-generator` alphabetically to `integration`. The submitter must be the owner. Review often takes months.
5. Optional: add the domain to [home-assistant/brands](https://github.com/home-assistant/brands) so the icon also appears in Settings → Devices & Services. HACS itself is satisfied by `brand/icon.png`.

Users can already install it as a **custom repository** without that PR.
