# Risk-Map Refactor & Architecture Documentation

This folder documents the modernized architecture of the **risk-map** framework, replacing the legacy scripts and notebooks with a modular, typed, and verifiable Python package in [src/risk_map](src/risk_map).

---

## Documentation Structure

Each document focuses on a specific subsystem or package layer without duplicating code:

| Document | Topic | Key Modules Covered |
|---|---|---|
| [refactor/01-architecture.md](refactor/01-architecture.md) | High-Level Architecture & Principles | System layers, package layout, SI units, and quality gates |
| [refactor/02-config.md](refactor/02-config.md) | Configuration & Physical Parameters | [src/risk_map/config/models.py](src/risk_map/config/models.py), [src/risk_map/config/enums.py](src/risk_map/config/enums.py), [src/risk_map/config/constants.py](src/risk_map/config/constants.py) |
| [refactor/03-uncertainty.md](refactor/03-uncertainty.md) | Uncertainty & Measurement Model | [src/risk_map/models/uncertainty.py](src/risk_map/models/uncertainty.py) |
| [refactor/04-spatial.md](refactor/04-spatial.md) | Spatial Geometry & Hydrology | [src/risk_map/spatial/flow.py](src/risk_map/spatial/flow.py), [src/risk_map/spatial/rivers.py](src/risk_map/spatial/rivers.py) |
| [refactor/05-graph.md](refactor/05-graph.md) | River Network & Topological Sweep | [src/risk_map/graph/network.py](src/risk_map/graph/network.py), [src/risk_map/graph/propagation.py](src/risk_map/graph/propagation.py) |

---

## Core Tenets

1. **Pure SI Units Throughout**: All distances in metres (m), discharges in cubic metres per second (m³/s), masses in kilograms (kg), and degradation rates in reciprocal seconds (s⁻¹).
2. **First-Order Uncertainty**: Native error propagation across dilutions and decays via [src/risk_map/models/uncertainty.py](src/risk_map/models/uncertainty.py).
3. **Single-Pass Mass Balance**: 1D advection-decay evaluated topologically in $O(V + E)$ while retaining granular per-source attribution.
4. **Automated Quality Gates**: Enforced by [scripts/check.sh](scripts/check.sh) covering Ruff, Ty, and Pytest.
