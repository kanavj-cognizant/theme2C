# Configuration & Physical Parameters

This document covers the configuration and domain parameter layer implemented in [src/risk_map/config](../src/risk_map/config).

---

## 1. Design & Objectives

In the legacy code, pollutant names, source types, and constants were scattered across multiple files ([pollutant_parameters/P_values.py](../pollutant_parameters/P_values.py), [pollutant_parameters/NH4_values.py](../pollutant_parameters/NH4_values.py), [utils/consentsRisk.py](../utils/consentsRisk.py)) with unvalidated string keys.

The refactored configuration layer provides:
- Strict enum definitions for all supported pollutants, sources, and aggregation operations.
- Pydantic models enforcing positive spatial thresholds and resolution bounds at runtime.
- Pure SI units across all environmental quality guidelines, degradation rates, and fill values.

---

## 2. Module Reference

### Enums ([src/risk_map/config/enums.py](../src/risk_map/config/enums.py))
- `PollutantType`: Identifies water quality determinands (`P` for total phosphorus / orthophosphate, `NH4` for ammoniacal nitrogen).
- `SourceType`: Categorizes point sources (`CSO`, `WWTW`) and diffuse sources (`AGRICULTURE`, `LIVESTOCK`, `URBAN_ROAD`, `URBAN_BUILDING`).
- `AggregationMethod`: Mathematical reduction modes across sources (`SUM`, `MEAN`, `FLOW_WEIGHTED`).

### Configuration Model ([src/risk_map/config/models.py](../src/risk_map/config/models.py))
- `PipelineConfig`: Validated, currently mutable parameters for river network discretization and spatial matching:
  - `river_resolution`: Target discretization length for river lines in metres (default `300.0`).
  - `max_outfall_distance`: Search radius cutoff for snapping outfall points to river lines in metres (default `700.0`).
  - `min_catchment_area`: Minimum catchment polygon area in square metres (default `20000.0`).
  - `min_flow_threshold`: Cutoff discharge threshold in cubic metres per second (m³/s, default `1e-6`) below which a reach is treated as dry, avoiding division-by-zero errors.
  - `data_dir`: File system path to the input data directory.
- `DEFAULT_CONFIG`: Exported default instance (`DEFAULT_CONFIG = PipelineConfig()`) used as the single source of truth for function argument defaults across spatial and graph modules.

### Physical Parameters ([src/risk_map/config/constants.py](../src/risk_map/config/constants.py))
- `PollutantParameters`: Container for determinand-specific physical and regulatory constants in standard SI units:
  - `guideline`: Environmental quality standard concentration in kg/m³ ($1\text{ mg/L} = 10^{-3}\text{ kg/m}^3$).
  - `degradation_rate`: In-stream first-order decay rate in reciprocal seconds ($s^{-1}$), converted from daily rates ($k / 86400$).
  - `wwtw_fill_value`: Baseline effluent concentration for consented wastewater treatment works in kg/m³.
  - `cso_fill_value`: Baseline effluent concentration for storm overflows in kg/m³.
  - `road_urban_emc`, `road_rural_emc`, `building_emc`: Event mean concentrations for diffuse runoff in kg/m³.
- Pre-configured instances: `P_CONSTANTS` and `NH4_CONSTANTS`, accessible via `POLLUTANT_LOOKUP`.

---

## 3. Testing

Verified by [tests/unit/test_config.py](../tests/unit/test_config.py), validating enum conversions, Pydantic constraint rejections on non-positive values, and SI constant conversions.

## 4. Implemented Contracts and Assumptions

**Status:** current behavior; stricter validation and immutability remain decisions
for review. `enums.py` owns vocabulary, `models.py` owns execution settings, and
`constants.py` owns pollutant parameter records. None performs data loading.

```python
from risk_map.config.models import PipelineConfig
from risk_map.config.constants import POLLUTANT_LOOKUP
from risk_map.config.enums import PollutantType

config = PipelineConfig(river_resolution=150.0)
parameters = POLLUTANT_LOOKUP[PollutantType.P]
```

`PipelineConfig` accepts the five keyword fields listed above. `data_dir` defaults
to `Path("data")`, relative to the process working directory. Construction does
not resolve paths, check existence, create directories, or access the network.
`PollutantParameters` requires all eight fields listed in section 2, including
`pollutant`; it has no field defaults. Both models expose Pydantic's
`model_dump()`, `model_dump_json()`, and `model_validate()` APIs.

- Enums are string-valued, case-sensitive vocabularies; invalid enum construction
  raises `ValueError`. Pydantic model input failures raise `ValidationError`.
- Numeric fields use `PositiveFloat`. Construction enforces positivity, including
  rejection of zero, but these are not strict-mode models or finite-only types.
  Numeric coercion is allowed and extra model fields are ignored by default.
- Neither model configures freezing or assignment validation. Shared
  `DEFAULT_CONFIG`, `P_CONSTANTS`, `NH4_CONSTANTS`, and `POLLUTANT_LOOKUP` can be
  mutated. Function defaults derived from them are evaluated when functions are
  defined; subsequent mutation is not a supported global configuration mechanism.
- `AggregationMethod` declares vocabulary only; it does not implement reductions
  or configure the current propagation function.
- `P` currently names phosphorus/orthophosphate parameters, not an automatic
  chemical-species conversion. Callers must ensure loads and guideline use the
  same species basis. Legacy source conversion parity remains to be verified.
- `ScenarioConfig`, source overrides, workspace configuration, and a serializable
  end-to-end run request are not implemented in this layer.

| API/file | Contract or assumption | Status | Verification/test | Open question |
|---|---|---|---|---|
| Enums | Exact supported strings and categories | Implemented | Enum tests in `test_config.py` | Future custom pollutants |
| `PipelineConfig` | Defaults and construction-time positivity | Implemented | `test_pipeline_config_defaults_and_validation` | Strictness, extra fields, finite values, immutability |
| `PollutantParameters` | SI guideline, decay, and concentration values | Implemented | `test_pollutant_physical_constants` checks selected constants | Remaining EMC values and species-basis parity |
| Serialization/path handling | Pydantic serialization; relative paths, no I/O | Implemented, not directly tested | Coverage gap | Server request schema and path policy |
