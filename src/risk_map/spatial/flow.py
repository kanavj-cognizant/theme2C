from typing import Protocol, runtime_checkable

import geopandas as gpd
import polars as pl


@runtime_checkable
class FlowEstimator(Protocol):
    """Abstract protocol for calculating river reach flow (m³/s) and travel speed (m/s)."""

    def estimate_flow(self, rivers: gpd.GeoDataFrame) -> pl.Series:
        """Estimate river discharge in cubic meters per second (m³/s)."""
        ...

    def estimate_flow_speed(self, rivers: gpd.GeoDataFrame) -> float | pl.Series:
        """Estimate flow travel speed in meters per second (m/s)."""
        ...


class LinearAccumulationFlowEstimator:
    """Baseline flow estimator using upstream accumulated length and constant flow speed."""

    def __init__(
        self,
        coeff: float = 1.476e-5,
        accum_col: str = "US_Accum",
        flow_speed: float = 0.77,
    ):
        self.coeff = coeff
        self.accum_col = accum_col
        self.flow_speed = flow_speed

    def estimate_flow(self, rivers: gpd.GeoDataFrame) -> pl.Series:
        if self.accum_col not in rivers.columns:
            raise KeyError(f"Column '{self.accum_col}' not found in rivers GeoDataFrame")
        return pl.Series("flow", rivers[self.accum_col].to_numpy() * self.coeff)

    def estimate_flow_speed(self, rivers: gpd.GeoDataFrame) -> float | pl.Series:
        return self.flow_speed
