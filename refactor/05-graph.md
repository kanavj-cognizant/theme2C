# River Network Graph & Single-Pass Topological Sweep

This document covers graph topology construction, single-pass mass transport, and source attribution in [src/risk_map/graph](src/risk_map/graph).

---

## 1. Principles & Mathematical Formulation

### Unifying Point and Diffuse Transport
In the legacy codebase, point sources triggered independent Breadth-First Searches for each outfall ($O(S \times V)$), computing heuristic flow-weighted concentrations, while diffuse sources accumulated mass down a topological order.

The refactored graph engine treats all pollutants as mass injection fluxes $L$ (kg/s). Because 1D steady-state advection-decay is linear:
$$\frac{d L}{d x} = - \frac{k}{v} L \implies L(x + \Delta x) = L(x) \cdot \exp\left(-\frac{k \Delta x}{v}\right)$$
The principle of superposition holds: total mass at any reach is the sum of all individual source contributions. Thus, a single topological sweep ($O(V + E)$) can propagate all point and diffuse sources simultaneously without losing granular per-source attribution.

### Node State & Nested Attribution Map
At each river node $n$, pollutant mass is structured hierarchically by `SourceType` (enum) and individual source identifier (`str`):
$$\mathbf{L}_n = \{ T: \{ s: L_{n, T, s} \pm \sigma_{L_{n, T, s}} \} \} \quad (\text{kg/s})$$
Where $T \in \text{SourceType}$ (e.g. `SourceType.CSO`, `SourceType.WWTW`, `SourceType.AGRICULTURE`) and $s$ is the specific asset/outfall ID (e.g. `"CSO_102"`, `"WWTW_A"`, `"diffuse"`).

Visiting nodes in topological order (headwaters to sea):
1. **Upstream Inflow**:
   $$L_{n, T, s}^{(\text{incoming})} = \sum_{p \in \text{predecessors}(n)} L_{p, T, s} \cdot \exp\left(-k \cdot \frac{\text{length}_{p \to n}}{v_{p \to n}}\right)$$
2. **Local Injections**:
   $$L_{n, T, s} = L_{n, T, s}^{(\text{incoming})} + L_{n, T, s}^{(\text{injected})}$$
3. **Concentration, Risk & Apportionment**:
   $$C_n = \frac{\sum_{T, s} L_{n, T, s}}{Q_n} \quad (\text{kg/m}^3), \quad \text{Risk}_n = \frac{C_n}{\text{Guideline}} \times 100\%$$
   $$\text{Attribution}_{T, s} = \frac{L_{n, T, s}}{\sum_{T', s'} L_{n, T', s'}} \times 100\% \quad (\text{granular by outfall})$$
   $$\text{Attribution By Type}_T = \frac{\sum_s L_{n, T, s}}{\sum_{T', s'} L_{n, T', s'}} \times 100\% \quad (\text{category rollup})$$

---

## 2. Module Reference

### Network Construction ([src/risk_map/graph/network.py](src/risk_map/graph/network.py))
- `build_river_graph`: Constructs a directed primal graph (`NetworkX.DiGraph`) from discretized river reaches:
  - Nodes are coordinate tuples $(x, y)$ in EPSG:27700.
  - Edges point downstream in the direction of digitized vertices.
  - Edges store physical attributes: `length` in metres, `flow_speed` in m/s, and `riverID`.
  - Nodes store `flow` in m³/s and `nodeID`.

### Topological Sweep & Results ([src/risk_map/graph/propagation.py](src/risk_map/graph/propagation.py))
- `ReachResult`: Dataclass holding the assessment result for a single river node:
  - `node`: Coordinate tuple $(x, y)$.
  - `flow`: River discharge measurement in m³/s.
  - `pollutant`: Evaluated `PollutantType` (`P` or `NH4`).
  - `source_loads`: Nested dict `dict[SourceType, dict[str, Measurement]]` of decayed mass loads in kg/s.
  - `total_load`: Aggregated mass load across all sources in kg/s.
  - `concentration`: Reach pollutant concentration in kg/m³.
  - `risk`: Environmental quality standard compliance ratio in percent.
  - `attribution`: Nested dict `dict[SourceType, dict[str, Measurement]]` of individual asset apportionment percentages (summing to 100%).
  - `attribution_by_type`: Dict `dict[SourceType, Measurement]` of category rollup percentages (e.g. total CSO % vs total WwTW % vs total Agriculture %).
- `propagate_loads`: Runs the single-pass topological advection-decay sweep using reach-specific lengths and flow speeds stored directly on each river edge. Returns `dict[tuple[float, float], ReachResult]`. Handles directed cycles in rare braided reaches gracefully.

---

## 3. Testing

Verified by [tests/unit/test_graph.py](tests/unit/test_graph.py):
- Graph topology: Node count, edge attributes, and flow propagation.
- Analytical mass balance: Verified against exact closed-form solutions on a 3-reach synthetic river, confirming source-specific exponential decay and exact 100% attribution summation.
