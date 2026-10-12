# Technical Documentation

This folder documents **how the code currently works**: how it is structured, how data moves through it, and what each computation does. It describes the code as it is today, so it can be used as a baseline before a refactor.

For the scientific rationale (why the model is built this way, validation, limitations) see the methodology document [../docs.md](../docs.md). For installation and usage see the top-level [README](../../README.md).

## Contents

| Document | What it covers |
|---|---|
| [01-architecture.md](./01-architecture.md) | Modules, responsibilities, dependencies, entry points, runtime/global state |
| [02-data-ingestion.md](./02-data-ingestion.md) | `get_and_transform_data.py`: downloads, transformations, files written to `data/` |
| [03-riskmap-pipeline.md](./03-riskmap-pipeline.md) | End-to-end run, the `RiskMap` class, river graph, catchment delineation, point/diffuse mapping |
| [04-risk-calculations.md](./04-risk-calculations.md) | Per-source load and risk formulas, units, distance decay, aggregation |
| [05-data-reference.md](./05-data-reference.md) | Schemas: files, lookup tables, constants, graph node attributes, key columns |
| [06-refactoring-notes.md](./06-refactoring-notes.md) | Observed coupling, hidden state, hazards and assumptions relevant to refactoring |

## Reading order

1. Start with [01-architecture.md](./01-architecture.md) for the big picture.
2. Read [03-riskmap-pipeline.md](./03-riskmap-pipeline.md) for the runtime data flow.
3. Use [04-risk-calculations.md](./04-risk-calculations.md) and [05-data-reference.md](./05-data-reference.md) as references.

## Conventions

- All spatial work is in **EPSG:27700** (British National Grid, metres). Other CRSs are used only transiently (EPSG:4326 for grid-reference conversion, elevation download and WhiteboxTools).
- "Point source" = WwTW or CSO (a single outfall location). "Diffuse source" = agriculture, livestock, roads, buildings (area-based).
- "Parameter" = the pollutant: `"P"` (orthophosphate) or `"NH4"` (ammoniacal nitrogen).
- Load is in **g/s**, concentration/guideline in **mg/l**, river flow in **m³/s**, distance in **m** unless stated.
- Paths are relative to the repository root.
- The code is driven from [Exploration.ipynb](../../Exploration.ipynb); there is no CLI or application entry point beyond `python get_and_transform_data.py` (data refresh only).
