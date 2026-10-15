# Uncertainty Propagation & Measurement Model

This document describes the uncertainty modeling and error propagation layer implemented in [src/risk_map/models/uncertainty.py](../src/risk_map/models/uncertainty.py).

---

## 1. Mathematical Foundation

Water quality models have substantial uncertainty in source emission loads ($L \pm \sigma_L$) and ambient river discharge ($Q \pm \sigma_Q$). Rather than relying on deterministic point estimates or computationally prohibitive Monte Carlo sweeps, the framework uses first-order Taylor expansion error propagation.

### Dilution
At any river reach with mass load $L$ (kg/s) and discharge $Q$ (m³/s):
$$C = \frac{L}{Q} \quad (\text{kg/m}^3)$$
For independent load and flow, the first-order variance satisfies:
$$\sigma_C^2 \approx \left(\frac{1}{Q}\right)^2 \sigma_L^2 + \left(-\frac{L}{Q^2}\right)^2 \sigma_Q^2$$
For correlated inputs, add $-2L\operatorname{Cov}(L,Q)/Q^3$.

### First-Order Decay
Along a reach of length $\Delta L$ with flow speed $v$ and degradation rate $k$ (s⁻¹), the travel time is $t = \Delta L / v$ (seconds). The decayed load is:
$$L(t) = L(0) \cdot e^{-k t}$$
When length, speed, and decay coefficient are deterministic, its propagated standard deviation is scaled by the same exponential factor:
$$\sigma_{L(t)} = \sigma_{L(0)} \cdot e^{-k t}$$

---

## 2. Module Reference ([src/risk_map/models/uncertainty.py](../src/risk_map/models/uncertainty.py))

- `Measurement`: Typed container wrapping an internal affine error representation from the `uncertainties` package.
  - Exposes `nominal` (nominal estimate, not necessarily a statistical mean after nonlinear operations) and `std_dev` (first-order standard deviation) as floats.
  - Overloads arithmetic operators (`+`, `-`, `*`, `/`) so linear combinations and products automatically compute first-order propagated standard deviations and covariance terms.
- `to_measurement`: Coercion utility converting floats, integers, or existing measurements into a canonical `Measurement`.
- `calculate_decay`: Applies exponential in-stream degradation given segment length, flow speed, and decay rate in SI units.
- `calculate_concentration`: Computes concentration ($L / Q$), accepting a `min_flow_threshold` (configured via `PipelineConfig.min_flow_threshold`, default $10^{-6}\text{ m}^3/\text{s}$) to guard against division-by-zero crashes on ephemeral or dry reaches. Reaches below this threshold evaluate to zero concentration.
- `calculate_risk`: Normalizes reach concentration against environmental quality guidelines ($C / \text{Guideline} \times 100\%$).

---

## 3. Testing

Verified by [tests/unit/test_uncertainty.py](../tests/unit/test_uncertainty.py), validating arithmetic operations against independent analytical variances, exponential decay rates, safe dry-stream handling, and guideline ratios.

## 4. Implemented API Contracts

**Status:** current scalar implementation; estimating uncertainty and selecting
plotting intervals remain outside this module's contract.

```python
Measurement(nominal: float, std_dev: float = 0.0)
Measurement.from_ufloat(val: Any) -> Measurement
to_measurement(val: MeasurementLike) -> Measurement
calculate_decay(load: MeasurementLike, length: float, flow_speed: float,
                degradation_rate: float) -> Measurement
calculate_concentration(load: MeasurementLike, river_flow: MeasurementLike,
                        min_flow_threshold: float = 1e-6) -> Measurement
calculate_risk(concentration: MeasurementLike, guideline: float) -> Measurement
```

`MeasurementLike` is `Measurement | AffineScalarFunc | float | int`.
`Measurement` supports addition, subtraction, multiplication and division,
including reversed scalar operations. It has no unit metadata or public variance
property; variance is `measurement.std_dev ** 2`.

```python
from risk_map.models.uncertainty import Measurement, calculate_concentration

load = Measurement(0.01, 0.001)
flow = Measurement(2.0, 0.2)
concentration = calculate_concentration(load, flow)
variance = concentration.std_dev**2
```

### Identity, Correlation and Ownership

- Separately constructed measurements are independent uncertain variables.
  Reusing a measurement preserves shared-variable correlation through arithmetic.
  Reconstructing one from just nominal value and standard deviation loses that
  identity; marginal variances are not a covariance-preserving interchange format.
- `to_measurement` returns an existing measurement unchanged, wraps an existing
  `AffineScalarFunc` without rebuilding it, and treats numeric scalars as exact.
  Other inputs raise `TypeError`.
- `uval` exposes the underlying uncertainties object; `from_ufloat` accepts it
  without runtime validation. There is no frozen or serialization contract.
- Negative standard deviation raises `ValueError`; finite values, physical units,
  and negative nominal loads are not validated here. General division is not
  protected against zero; only the concentration helper applies a cutoff.
- Import configures a process-wide warning filter for uncertainties' zero-standard-
  deviation warning. This is an existing import side effect, not a pure module.

### Boundary Behavior

- `calculate_decay` returns the coerced load unchanged if length or speed is
  nonpositive. Otherwise it uses `math.exp` with deterministic float parameters.
  Negative decay rates are not rejected by this helper and imply growth.
- `calculate_concentration` returns an exact zero when nominal flow is **less
  than or equal to** the threshold, including negative flow. It does not inspect
  the flow uncertainty interval. The threshold itself is not validated here.
- `calculate_risk` rejects nonpositive guidelines with `ValueError`; it does not
  clamp risks or reject negative concentrations.
- First-order propagation can be misleading near zero denominators, wide
  uncertainty, or nonlinear thresholds. Standard deviation is not automatically
  a confidence interval; no distribution or coverage probability is supplied.
- These helpers propagate uncertainty in scalar loads and, for concentration,
  flow. The graph currently coerces its flow and transport attributes to floats;
  end-to-end uncertain hydrology is not yet implemented.

| API/file | Contract or assumption | Status | Verification/test | Open question |
|---|---|---|---|---|
| `Measurement`, coercion | Independent addition, scalar/raw-ufloat conversion, identity | Implemented | Creation and conversion tests in `test_uncertainty.py` | Correlated cancellation, reversed arithmetic, invalid inputs |
| `calculate_decay` | Deterministic coefficient scales nominal and deviation | Implemented | `test_calculate_decay` | Nonpositive length/speed policy and uncertain transport parameters |
| Concentration/risk | Ratio variance, zero-flow output, guideline normalization | Implemented | Concentration and risk tests | Exact cutoff, negative flow, invalid guideline, near-zero uncertainty policy |
| Export | No covariance-preserving table or JSON contract | Deferred | No export tests | Preserve shared input identity across pipeline boundaries |
