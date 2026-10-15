# River Network Graph & Single-Pass Topological Sweep

This document covers graph topology construction, single-pass mass transport, and source attribution in [src/risk_map/graph](../src/risk_map/graph).

---

## 1. Principles & Mathematical Formulation

### Unifying Point and Diffuse Transport
In the legacy codebase, point sources triggered independent Breadth-First Searches for each outfall ($O(S \times V)$), computing heuristic flow-weighted concentrations, while diffuse sources accumulated mass down a topological order.

The refactored graph engine treats all pollutants as mass injection fluxes $L$ (kg/s). Because 1D steady-state advection-decay is linear:
$$\frac{d L}{d x} = - \frac{k}{v} L \implies L(x + \Delta x) = L(x) \cdot \exp\left(-\frac{k \Delta x}{v}\right)$$
The principle of superposition holds for fixed flows and decay parameters: total mass at any reach is the sum of individual source contributions. The graph is ordered once, but attribution arithmetic scales with the number of source entries carried along each edge. Full source attribution is not an overall $O(V + E)$ operation independent of source count.

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

### Network Construction ([src/risk_map/graph/network.py](../src/risk_map/graph/network.py))
- `build_river_graph`: Constructs a directed primal graph (`NetworkX.DiGraph`) from discretized river reaches:
  - Nodes are coordinate tuples $(x, y)$ in EPSG:27700.
  - Edges point downstream in the direction of digitized vertices.
  - Edges store physical attributes: `length` in metres, `flow_speed` in m/s, and `riverID`.
  - Nodes store `flow` in m³/s and `nodeID`.

### Topological Sweep & Results ([src/risk_map/graph/propagation.py](../src/risk_map/graph/propagation.py))
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
- `propagate_loads`: Runs a topological advection-decay sweep using reach-specific lengths and flow speeds stored on edges. Returns `dict[tuple[float, float], ReachResult]`. Cyclic networks are not a supported scientific contract; see limitations below.

## Implemented API Contracts

**Status:** describes current implementation, not approval of unresolved behavior.
Public imports are available from `risk_map.graph`; implementation modules own
construction (`network.py`) and transport (`propagation.py`).

```python
build_river_graph(rivers: gpd.GeoDataFrame,
                           flow_estimator: FlowEstimator | None = None) -> nx.DiGraph
propagate_loads(graph: nx.DiGraph, injected_loads: InjectedLoads,
                        parameters: PollutantParameters,
                        min_flow_threshold: float = DEFAULT_CONFIG.min_flow_threshold
                        ) -> dict[tuple[float, float], ReachResult]
```

`InjectedLoads` maps coordinate-pair node keys to
`{SourceType: {source_id: Measurement | float}}`, in kg/s for one pollutant per
call. Reusing a source ID within a type combines its contributions; IDs in
different types remain distinct. Injections for nodes absent from the graph are
currently ignored. No runtime source-ID or source-type validation is performed.

For example, given a prepared graph with an injection node `(0.0, 0.0)`:

```python
from risk_map.config.constants import P_CONSTANTS
from risk_map.config.enums import SourceType
from risk_map.graph.propagation import propagate_loads

results = propagate_loads(graph, {(0.0, 0.0): {SourceType.CSO: {"outfall-1": 0.01}}}, P_CONSTANTS)
source_load = results[(0.0, 0.0)].source_loads[SourceType.CSO]["outfall-1"]
```

### Construction and Ownership

- Input geometry must already use metre coordinates and downstream vertex order;
   construction neither validates EPSG:27700 nor reprojects or repairs topology.
   Endpoint coordinate equality determines connectivity, not geometric crossings.
- Construction copies the input frame. A supplied estimator fills missing `flow`
   and `flow_speed` columns only; existing columns win. Missing speed raises
   `KeyError`. Missing flow defaults to zero at graph construction.
- Node discharge is the maximum incident edge discharge, not the sum of tributary
   flows. `nodeID` is assigned in iteration order and is not a persistent identity.
   Node `riverID` is a set of incident river identifiers.
- The returned graph is a mutable `DiGraph`, not a multigraph. Parallel reaches
   with identical directed endpoints cannot be represented independently.
- Propagation does not write results onto the input graph or injection mapping.
   Returned `ReachResult` instances and their dictionaries are mutable. Nested
   load dictionaries are copied, but measurement objects are not deep-copied.

### Numerical Behavior and Limits

- Node `flow`, edge `flow_speed`, and edge `length` are converted to floats;
   uncertain flow/speed are not supported here. Missing node flow or edge length
   defaults to zero. Missing edge speed raises `KeyError` during traversal.
- Local injection occurs at the node, after incoming-edge decay; no fractional
   edge injection or point-snap adapter is implemented in this module.
- Positive nominal total load produces source and category percentages. Zero or
   negative nominal total leaves attribution dictionaries empty. This is not an
   explicit physical policy for negative source loads, which are not rejected.
- Dry nodes return zero concentration/risk through `calculate_concentration`,
   but their loads still propagate downstream. This must not be interpreted as
   demonstrated zero environmental impact.
- **Agreed topology contract:** `propagate_loads` raises `ValueError` for a
   directed cycle (including a self-loop) or any node with more than one
   downstream successor. Validation covers every component before numerical
   propagation, does not remove edges, and leaves the graph unchanged. Confluences,
   disconnected catchments, and isolated nodes are allowed. Network construction
   remains separate; this guard applies at the propagation boundary.
- Branch flow fractions and cyclic routing are not implemented. Rejecting splits
   prevents copying the full upstream mass into multiple branches. Error messages
   identify a cycle or a downstream split; split errors include an offending node.
- Risk uses one guideline and one pollutant per call. Individual source loads
   and percentage shares are retained, but source-specific concentration/risk
   fields, multi-pollutant aggregation, scenarios, caching, and result export are
   not implemented here.

### Contract Evidence and Open Decisions

| API/file | Contract or assumption | Status | Verification/test | Open question |
|---|---|---|---|---|
| `build_river_graph` | Directed endpoint topology, edge speed, maximum incident flow | Implemented | `test_build_river_graph_structure`, `test_missing_flow_speed_raises_error` | Junction-flow and parallel-reach policy |
| `propagate_loads` | Chain decay, local injection, individual CSO attribution | Implemented | `test_topological_propagation_analytical_mass_balance` | Tests for confluences, mixed source types, and absent injection nodes |
| `ReachResult` | Mutable result with uncertainty-bearing loads and percentages | Implemented | Existing chain test checks nominal values | Correlation, ownership, and uncertainty assertions |
| Topology validation | Reject cycles and downstream splits before calculation; allow confluences and disconnected catchments | Agreed | Topology rejection and acceptance tests in `test_graph.py` | Branch routing remains deferred |
| Performance | Source-dependent time and retained-output memory | Unmeasured | No benchmark in this suite | Representative catchment and source-count baseline |

---

## 3. Testing

Verified by [tests/unit/test_graph.py](../tests/unit/test_graph.py):
- Graph topology: Node count, edge attributes, and flow propagation.
- Analytical mass balance: Verified against exact closed-form solutions on a 3-reach synthetic river, confirming source-specific exponential decay and exact 100% attribution summation.
- Topology contract: Rejection tests cover self-loops, directed cycles, splits,
  split/rejoin networks, and invalid disconnected components, without graph
  mutation or numerical calculation. Acceptance tests cover a confluence,
  disconnected catchment, and isolated node.

### Cached Network Observation (2026-10-15)

A read-only check of `data/rivers.parquet` found 183,335 line parts in EPSG:27700,
186,891 exact-endpoint nodes, 3,556 weakly connected components, no directed cycles
or self-loops, and **two nodes with multiple downstream successors**. There were
no duplicate directed endpoint pairs. This check used the stored line directions
and exact endpoints before segmentation, without repairs or downloads; it does
not establish topology for every clipped/segmented catchment or future data
version. The agreed guard rejects an affected input rather than guessing routing.
