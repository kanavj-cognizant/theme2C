# 5. Data Reference

## 5.1 Files under `data/`

### Committed lookup tables (read by the code)

| File | Index / key columns | Other columns | Read by | Purpose |
|---|---|---|---|---|
| `crop_categories.csv` | `code` (CROME `lucode`) | `category`, `crop_category` (float) | `get_crome_data` | CROME land-use code -> crop category used in load lookups. Rows with `category == "N/A"` are dropped before the join. |
| `soil_lookup.csv` | `HOST` | `soil_category` | `get_soil_data` | HOST soil class -> `soil_category`. |
| `soil_host_data.zip` | - | `HOST`, geometry | `get_soil_data` | Soil polygons (read directly with geopandas). |
| `agriculture_load_lookup.csv` | `crop_category`, `soil_category`, `rainfall_category` | `Pathway` (0/1), `NH4_load`, `P_load` (kg/m²/yr) | `agricultureRisk` | Arable/grass land load per unit area. Multiple rows per key (one per pathway). |
| `livestock_excreta.csv` | `OPCAT_ID`, `crop_category` | `livestock_category`, `excreta_per_ha` | `agricultureRisk` | Livestock excreta per hectare per operational catchment and land type. |
| `livestock_excreta_load.csv` | `crop_category`, `soil_category`, `rainfall_category`, `livestock_category` | `Pathway`, `NH4_load`, `P_load` | `agricultureRisk` | Load per unit excreta. |
| `road_width_lookup.csv` | `classification` | `width` (m) | `urbanRisk` | OS road classification -> assumed carriageway width. |
| `livestock_lookup.csv` | `Stock` | `livestock_category` (1 Beef, 2 Dairy, 3 Pigs, 4 Sheep, 5 Poultry) | not read by code | Reference decoding of `livestock_category`. |

### Generated / downloaded (git-ignored)

| Path | Written by | Content |
|---|---|---|
| `data/annual/` | `get_cso_annual_data` | Extracted EA annual-return xlsx; `cso.parquet` = annual returns with geometry (pre-merge). |
| `data/consentsdb/` | `get_consents_data` | Extracted `.accdb`, `consents.csv`, `determinands.csv`. |
| `data/rivers.parquet` | `get_rivers_data` | ORN rivers, EPSG:27700. |
| `data/cso.parquet` | `get_and_transform_data` | Merged CSOs (annual returns + consents key). **Different file from `data/annual/cso.parquet`.** |
| `data/wwtw.parquet` | `get_and_transform_data` | WwTW consents with geometry. |
| `data/determinands.parquet` | `get_and_transform_data` | Filtered, exploded determinands. |
| `data/os_roads.parquet`, `os_buildings.parquet`, `os_builtup.parquet` | `get_OS_data` | OS vector layers, EPSG:27700. |
| `data/wbe/` | `catchment_delineation` | WhiteboxTools working dir: `raster.tif`, `riskmap.shp`, `polygons.shp` and intermediates. |
| `data/tiles/` | `elevatr` | DEM tile cache. |

## 5.2 Key tables and their identifiers

| Table | Index | Important columns |
|---|---|---|
| `cso` (`data/cso.parquet`) | `Unique ID` (annual-return id) | `ID` (consents key, may be NaN), `SpillDuration`, `Counted spills using 12-24h count method`, `Site Name (EA Consents Database)`, geometry |
| `wwtw` (`data/wwtw.parquet`) | `ID = PERMIT_NUMBER_EFFLUENT_NUMBER_OUTLET_NUMBER` | `DISCHARGE_SITE_TYPE_CODE`, `DISCHARGE_SITE_NAME`, `OUTLET_GRID_REF`, geometry |
| `determinands` | `PERMIT_REF_EFFLUENT_NUMBER_OUTLET_NUMBER` (matches `wwtw` index; multiple rows per key) | `DETE_CODE`, `DETE`, `CODE`, `VALUE`, `VERSION` |
| rivers (`ORN`) | row index | `RivID` (used as `id_col`), `US_Accum`, geometry |

Join keys used by the risk modules:

- `wwtw` -> `determinands`: **index to index**, after filtering `determinands` by `DETE_CODE` and `CODE`.
- `cso` -> `determinands` (weir setting): `cso["ID"]` against the determinands index (`on="ID"`).
- Risk callables look rows up by `.loc[id, column]`, where `id` is the source's index value (`ID` for WwTWs, `Unique ID` for CSOs).

## 5.3 Determinands used

| `DETE_CODE` | Meaning | Statistic (`CODE`) used | Used by |
|---|---|---|---|
| 348 | Phosphorus, total as P | `MEAN VALUE` | WwTW P |
| 111 | Ammoniacal nitrogen as N | `MAXIMUM VALUE` | WwTW NH4 |
| 7782 | Dry weather flow | `MAXIMUM VALUE` | WwTW DWF |
| 8174 | Weir setting | `MINIMUM VALUE` | CSO weir setting |
| 7729, 135, 85, 6051, 6057, 3647, 6425 | DWF 3-in-5, suspended solids, BOD, iron, aluminium, flow to full treatment, daily total flow | - | Retained in `determinands.parquet` but unused by the model |

## 5.4 Constants

### `pollutant_parameters/P_values.py`

| Constant | Value | Unit |
|---|---|---|
| `PHOSPHORUS_TO_ORTHOPHOSPHATE_EFFLUENT` | 0.785 | ratio |
| `P_GUIDELINE` | 0.87 | mg/l |
| `P_DEGRADATION_COEFF` | 0.04 | /day |
| `WWTW_PHOSPHORUS_FILL_VALUE` | 5.0 | mg/l |
| `CSO_PHOSPHORUS_FILL_VALUE` | 3.0 | mg/l |
| `ROAD_P_RUNOFF`, `BUILD_P_RUNOFF` | 0.1, 0.1 | mg/l |
| `FLOW_SPEED` | 0.77 | m/s |

### `pollutant_parameters/NH4_values.py`

| Constant | Value | Unit |
|---|---|---|
| `NH4_GUIDELINE` | 6.0 | mg/l |
| `NH4_DEGRADATION_COEFF` | 0.42 | /day |
| `WWTW_NH4_FILL_VALUE` | 2.6 | mg/l |
| `CSO_NH4_FILL_VALUE` | 13 | mg/l |
| `ROAD_URBAN_NH4_RUNOFF`, `ROAD_RURAL_NH4_RUNOFF`, `BUILD_NH4_RUNOFF` | 0.8, 0.65, 0.2 | mg/l |
| `FLOW_SPEED` | 0.77 | m/s |

### `utils/consentsRisk.py`

| Constant | Value |
|---|---|
| `WWTW_DWF_FILL_VALUE` (A2, water company) | 20.0 m³/day |
| `WWTW_PRIVATE_A0_DWF_FILL_VALUE` | 0.8 m³/day |
| `WWTW_PRIVATE_A1_DWF_FILL_VALUE` | 7 m³/day |
| `CSO_WEIR_SETTING_FILL_VALUE` | 20 l/s |

### Other module-level constants

| Constant | Value | Where |
|---|---|---|
| `FLOW_ACCUM_COEFF` | 1.476e-5 | `utils/rivers.py` |
| `RURAL_ROAD_RIVER_DISTANCE` | 200 m | `utils/urbanRisk.py` |
| `GRASS`, `ROUGH`, `ARABLE` | `[4]`, `[0]`, `[1,2,3,5..13]` | `utils/agricultureRisk.py` |
| `OUTPUT_EPSG` | 27700 | `get_and_transform_data.py` |
| catchment `min_area` | 20 000 m² | `RiskMap.delineate_catchments` |
| stream threshold / snap distance / DEM zoom / DEM buffer | 1000 cells / 0.001° / 12 / 0.15° | `utils/catchment_delineation.py` |
| CSO matching buffer / fuzzy threshold | 5000 m / score > 92 | `merge_annual_and_consents` |
| Notebook: `RIVER_RESOLUTION`, `MAX_OUTFALL_DIST_FROM_RIVER`, `MAX_RISK_DISTANCE` | 300 m, 700 m, inf | `Exploration.ipynb` |

Rainfall classes (`rainfall_category` from annual `pr` in mm): `>1500`=5, `>1200`=4, `>900`=3, `>700`=2, `>600`=1, otherwise 0.

## 5.5 River graph node attributes

Nodes are keyed by their `(x, y)` coordinate tuple. After a full notebook run a node carries:

| Attribute | Type | Set by | Meaning |
|---|---|---|---|
| `nodeID` | int | momepy | Row index in the risk-map GeoDataFrame; used as `river_index` for catchment mapping |
| `flow` | float | `river_to_graph` | Max flow of incident edges (m³/s) |
| `riverID` | set | `river_to_graph` | ORN ids of incident edges |
| `csoInfo`, `wwtwInfo` | `dict[(id, flow)] -> distance_m` | `add_point_info_to_map` | Point sources affecting the node |
| `agri_{P,NH4}_load`, `livestock_{P,NH4}_load`, `urban_road_{P,NH4}_load`, `urban_build_{P,NH4}_load` | float (g/s) | `add_diffuse_info_to_map` | Node's share of catchment load |
| `{cso,wwtw,agri,livestock,urban_road,urban_build}_{P,NH4}_Risk` | float | `add_all_risk` | Per-source risk |
| `P_Risk`, `NH4_Risk`, `Risk` | float | `add_all_risk` | Aggregates |

Edge attributes: `riverID`, `flow`, `mm_len` (m), `geometry`.

`RiskMap.get_risk_map()` returns node points with these attributes as columns (dict and set cells converted to lists).

## 5.6 Key columns in intermediate tables

| Table | Columns that the code relies on |
|---|---|
| `rivers_split` | `riverID`, `flow`, `geometry`; `closestEdge` is added in place by point mapping |
| `joined_catchments` | index `FID_poly`; geometry column named `poly` |
| `map_poly_join` | `FID_poly`, `FID_to_join`, `river_index` |
| `mapping_points` | `river_index`, `geometry`, `FID`, `poly` |
| load GeoDataFrame returned by a `load_method` | **`FID_poly`** and **`load`** (g/s); anything else is ignored by `RiskMap` |
| land-cover layer | `crop_category`, `soil_category`, `rainfall_category`, geometry |
