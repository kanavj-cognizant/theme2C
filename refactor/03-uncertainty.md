# Uncertainty Propagation & Measurement Model

This document describes the uncertainty modeling and error propagation layer implemented in [src/risk_map/models/uncertainty.py](src/risk_map/models/uncertainty.py).

---

## 1. Mathematical Foundation

Water quality models have substantial uncertainty in source emission loads ($L \pm \sigma_L$) and ambient river discharge ($Q \pm \sigma_Q$). Rather than relying on deterministic point estimates or computationally prohibitive Monte Carlo sweeps, the framework uses first-order Taylor expansion error propagation.

### Dilution
At any river reach with mass load $L$ (kg/s) and discharge $Q$ (m³/s):
$$C = \frac{L}{Q} \quad (\text{kg/m}^3)$$
The variance of the resulting concentration satisfies:
$$\sigma_C^2 \approx \left(\frac{1}{Q}\right)^2 \sigma_L^2 + \left(-\frac{L}{Q^2}\right)^2 \sigma_Q^2$$

### First-Order Decay
Along a reach of length $\Delta L$ with flow speed $v$ and degradation rate $k$ (s⁻¹), the travel time is $t = \Delta L / v$ (seconds). The decayed load is:
$$L(t) = L(0) \cdot e^{-k t}$$
Its propagated standard deviation is scaled by the same exponential factor:
$$\sigma_{L(t)} = \sigma_{L(0)} \cdot e^{-k t}$$

---

## 2. Module Reference ([src/risk_map/models/uncertainty.py](src/risk_map/models/uncertainty.py))

- `Measurement`: Typed container wrapping an internal affine error representation from the `uncertainties` package.
  - Exposes `nominal` (expected mean value) and `std_dev` (standard deviation) as standard floats.
  - Overloads arithmetic operators (`+`, `-`, `*`, `/`) so linear combinations and products automatically compute first-order propagated standard deviations and covariance terms.
- `to_measurement`: Coercion utility converting floats, integers, or existing measurements into a canonical `Measurement`.
- `calculate_decay`: Applies exponential in-stream degradation given segment length, flow speed, and decay rate in SI units.
- `calculate_concentration`: Computes concentration ($L / Q$), accepting a `min_flow_threshold` (configured via `PipelineConfig.min_flow_threshold`, default $10^{-6}\text{ m}^3/\text{s}$) to guard against division-by-zero crashes on ephemeral or dry reaches. Reaches below this threshold evaluate to zero concentration.
- `calculate_risk`: Normalizes reach concentration against environmental quality guidelines ($C / \text{Guideline} \times 100\%$).

---

## 3. Testing

Verified by [tests/unit/test_uncertainty.py](tests/unit/test_uncertainty.py), validating arithmetic operations against independent analytical variances, exponential decay rates, safe dry-stream handling, and guideline ratios.
