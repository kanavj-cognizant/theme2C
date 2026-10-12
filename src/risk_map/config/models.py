from pathlib import Path

from pydantic import BaseModel, Field, PositiveFloat


class PipelineConfig(BaseModel):
    """Global configuration for river discretization and spatial analysis (SI units: m, s, kg)."""

    river_resolution: PositiveFloat = 300.0  # m
    max_outfall_distance: PositiveFloat = 700.0  # m
    min_catchment_area: PositiveFloat = 20000.0  # m2
    data_dir: Path = Field(default_factory=lambda: Path("data"))
