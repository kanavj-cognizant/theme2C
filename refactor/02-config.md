# Configuration & Physical Parameters

This document covers the configuration and domain parameter layer implemented in [src/risk_map/config](src/risk_map/config).

---

## 1. Design & Objectives

In the legacy code, pollutant names, source types, and constants were scattered across multiple files ([pollutant_parameters/P_values.py](pollutant_parameters/P_values.py), [pollutant_parameters/NH4_values.py](pollutant_parameters/NH4_values.py), [utils/consentsRisk.py](utils/consentsRisk.py)) with unvalidated string keys.

The refactored configuration layer provides:
- Strict enum definitions for all supported pollutants, sources, and aggregation operations.
- Pydantic models enforcing positive spatial thresholds and resolution bounds at runtime.
- Pure SI units across all environmental quality guidelines, degradation rates, and fill values.

---

## 2. Module Reference

### Enums ([src/risk_map/config/enums.py](src/risk_map/config/enums.py))
- `PollutantType`: Identifies water quality determinands (`P` for total phosphorus / orthophosphate, `NH4` for ammoniacal nitrogen).
- `SourceType`: Categorizes point sources (`CSO`, `WWTW`) and diffuse sources (`AGRICULTURE`, `LIVESTOCK`, `URBAN_ROAD`, `URBAN_BUILDING`).
- `AggregationMethod`: Mathematical reduction modes across sources (`SUM`, `MEAN`, `FLOW_WEIGHTED`).

### Configuration Model ([src/risk_map/config/models.py](src/risk_map/config/models.py))
- `PipelineConfig`: Global immutable parameters for river network discretization and spatial matching:
  - `river_resolution`: Target discretization length for river lines in metres (default `300.0`).
  - `max_outfall_distance`: Search radius cutoff for snapping outfall points to river lines in metres (default `700.0`).
  - `min_catchment_area`: Minimum catchment polygon area in square metres (default `20000.0`).
  - `min_flow_threshold`: Cutoff discharge threshold in cubic metres per second (m³/s, default `1e-6`) below which a reach is treated as dry, avoiding division-by-zero errors.
  - `data_dir`: File system path to the input data directory.
- `DEFAULT_CONFIG`: Exported default instance (`DEFAULT_CONFIG = PipelineConfig()`) used as the single source of truth for function argument defaults across spatial and graph modules.

### Physical Parameters ([src/risk_map/config/constants.py](src/risk_map/config/constants.py))
- `PollutantParameters`: Container for determinand-specific physical and regulatory constants in standard SI units:
  - `guideline`: Environmental quality standard concentration in kg/m³ ($1\text{ mg/L} = 10^{-3}\text{ kg/m}^3$).
  - `degradation_rate`: In-stream first-order decay rate in reciprocal seconds ($s^{-1}$), converted from daily rates ($k / 86400$).
  - `wwtw_fill_value`: Baseline effluent concentration for consented wastewater treatment works in kg/m³.
  - `cso_fill_value`: Baseline effluent concentration for storm overflows in kg/m³.
  - `road_urban_emc`, `road_rural_emc`, `building_emc`: Event mean concentrations for diffuse runoff in kg/m³.
- Pre-configured instances: `P_CONSTANTS` and `NH4_CONSTANTS`, accessible via `POLLUTANT_LOOKUP`.

---

## 3. Testing

Verified by [tests/unit/test_config.py](tests/unit/test_config.py), validating enum conversions, Pydantic constraint rejections on non-positive values, and SI constant conversions.
