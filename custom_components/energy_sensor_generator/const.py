"""Constants for the Energy Sensor Generator integration."""

DOMAIN = "energy_sensor_generator"
STORAGE_FILE = "energy_sensor_generator.json" 

# Configuration keys
CONF_DEBUG_LOGGING = "debug_logging"
CONF_USE_STATISTICAL = "use_statistical_calculation"
# Removed redundant point sampling options - simplified to just use statistical calculation toggle 
CONF_CREATE_SYNTHETIC_GRID_TOTAL = "create_synthetic_grid_total"