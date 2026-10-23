"""Constants for the Energy Sensor Generator integration."""

DOMAIN = "energy_sensor_generator"
STORAGE_FILE = "energy_sensor_generator.json"

# Global settings (config entry options)
CONF_SAMPLE_INTERVAL = "sample_interval"
CONF_DEBUG_LOGGING = "debug_logging"
CONF_USE_STATISTICAL = "use_statistical_calculation"
CONF_CREATE_SYNTHETIC_GRID_TOTAL = "create_synthetic_grid_total"
CONF_FORCE_STATISTICAL_ONLY = "force_statistical_only"
CONF_STAT_LOOKBACK_MINUTES = "stat_initial_lookback_minutes"
CONF_MAX_ENERGY_PER_HOUR = "max_energy_per_hour"  # Maximum kWh per hour to prevent spikes
# Highest plausible power reading (W): a global setting, and per power sensor source
CONF_MAX_POWER_W = "max_power_w"

DEFAULT_SAMPLE_INTERVAL = 60
DEFAULT_STAT_LOOKBACK_MINUTES = 30

# Subentry data keys
CONF_SOURCE_ENTITY_ID = "source_entity_id"
CONF_SWITCH_ENTITY_ID = "switch_entity_id"
CONF_POWER_W = "power_w"
CONF_PERIODS = "periods"
CONF_NAME = "name"
CONF_ADD_AMOUNT = "add_amount"
CONF_PRICE_ID = "id"
CONF_ENERGY_COUNTER = "energy_counter"

# Internal safeguards
# Maximum gap (seconds) we are willing to bridge with energy calculations.
# Gaps longer than max(this, 3 x sample_interval) indicate a restart or an
# offline source; we resync tracking instead of guessing the missing energy.
POINT_SAMPLING_MAX_GAP_SECONDS = 600
