# Spatial Hydrology & River Geometry

This document covers the spatial discretization, point-source snapping, and flow estimation layer in [src/risk_map/spatial](src/risk_map/spatial).

---

## 1. Design & Objectives

The spatial subsystem transforms raw vector datasets into a discretized mesh suitable for graph modeling:
- River lines are discretized into uniform segments bounded by a maximum target resolution.
- Discrete point sources (CSOs, WwTWs) are projected onto their nearest river line using spatial indexes.
- River discharge ($Q$) and flow velocity ($v$) are decoupled from configuration through an abstract protocol.

---

## 2. Module Reference

### Flow Estimation Strategy ([src/risk_map/spatial/flow.py](src/risk_map/spatial/flow.py))
- `FlowEstimator`: Protocol defining the interface for computing reach discharge and velocity:
  - `estimate_flow(rivers)`: Returns reach discharge in m³/s as a Polars Series.
  - `estimate_flow_speed(rivers)`: Returns reach flow velocity in m/s as a float or Polars Series.
- `LinearAccumulationFlowEstimator`: Baseline implementation estimating discharge from upstream accumulated river length:
  $$Q = \text{US\_Accum} \times \text{coeff}$$
  Can be replaced by gauge assimilation, Manning equation routing, or runoff models without modifying river network topology.

### River Discretization & Point Snapping ([src/risk_map/spatial/rivers.py](src/risk_map/spatial/rivers.py))
- `split_rivers`: Segments continuous LineStrings into segments of maximum length `resolution`, preserving cumulative line length, coordinates, and link attributes.
- `snap_points_to_rivers`: Projects points onto their nearest river reach within a cutoff search distance:
  - Uses GeoPandas spatial nearest joins (`sjoin_nearest`) backed by C-level R-trees.
  - Uses vectorized Shapely 2.0 primitives (`shortest_line` and `get_point`) to compute perpendicular projection coordinates on the reach.
  - Avoids Python-level row iteration.
  - Assigns `river_index`, `snapped_point`, and `snap_distance` in metres.

---

## 3. Testing

- [tests/unit/test_flow.py](tests/unit/test_flow.py): Validates protocol conformity, linear accumulation calculations, and Polars return types.
- [tests/unit/test_rivers.py](tests/unit/test_rivers.py): Validates LineString segment length bounds, exact total length conservation, and point-to-river snapping across all reach indices.
