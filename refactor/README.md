# Risk-Map Refactor & Architecture Documentation

This folder documents the target architecture and current implementation of
**risk-map** in [src/risk_map](../src/risk_map). The legacy application has not yet
been replaced by an end-to-end refactored runner.

---

## Documentation Structure

Each document focuses on a specific subsystem or package layer without duplicating code:

| Document | Topic | Key Modules Covered |
|---|---|---|
| [01-architecture.md](01-architecture.md) | Architecture and workflow | Implemented/proposed file map, dependencies, review gates, migration |
| [02-config.md](02-config.md) | Configuration and parameters | Enums, Pydantic models, defaults, validation and SI conversions |
| [03-uncertainty.md](03-uncertainty.md) | Scalar uncertainty | Measurement APIs, correlations, decay/dilution/risk, limitations |
| [04-spatial.md](04-spatial.md) | Spatial geometry and hydrology | Flow, splitting, snapping, delineation, grouping, output schemas |
| [05-graph.md](05-graph.md) | Network and propagation | Graph construction, source attribution, topology limitations |

Each subsystem guide records implemented signatures, a caller example, ownership
and side effects, assumptions, coverage evidence, and open decisions. "Implemented"
describes behavior; it does not mean every edge case is tested or the contract has
been approved. Catchment/grouping integration remains provisional. Test references
describe inspected assertions, not a claim that tests were executed for this audit.

---

## Core Tenets

1. **Explicit Units**: Core calculations use m, m3/s, kg/s, kg/m3, and s^-1.
	Whitebox/elevation preparation has documented degree/cell-count exceptions.
2. **First-Order Uncertainty**: Scalar load/flow arithmetic preserves shared-variable
	correlations; graph flow and transport parameters are currently deterministic.
3. **Source Attribution**: Transport retains each supplied source ID. Arithmetic
	and output memory scale with the source entries propagated, not just graph size.
4. **Review Before Code**: Document and agree contracts, review tests, then
	implement; bounded experiments resolve questions without silently fixing APIs.
5. **Evidence-Based Verification**: [scripts/check.sh](../scripts/check.sh) runs
	Ruff, Ty (with `--fix`), and unit tests. Legacy/new catchment equivalence remains
	a separate verification task, not an existing guarantee.
