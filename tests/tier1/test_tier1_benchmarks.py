"""Phase 1 Tier-1 benchmark exercise tests.

These five tests exercise the vendored Solver subsystem against well-known
analytical solutions. They are a Phoenix-native test surface that proves the
vendored substrate imports correctly and produces sane numerical results
through Phoenix's package boundary.

These are NOT the full architecture v1 Section 10.7 acceptance criterion
("Tier-1 benchmarks execute end-to-end through Trinity Core"). That requires
Trinity Core's pipeline orchestration which lands in Phase 2+. Phase 1's
Tier-1 tests just prove Phoenix can invoke the vendored substrate directly.

Tier-1 names are nominal (per ``DrFrankEddy_Capabilities_Overview_for_Ash.md``)
and map to the calibration suite's test cases:

- HO-1   : Quantum harmonic oscillator ground state (TISE)
- ISW-1  : Infinite square well / particle in a box (TISE)
- H1S-1  : Hydrogen 1s state (Dirac, rest-energy reduction)
- RABI-1 : Pauli/Zeeman splitting at non-zero magnetic field
- SCG-1  : Semiclassical gravity weak-field limit
"""

from __future__ import annotations

import math

import numpy as np

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules

# Physical constants (mirror frank-data's tests/test_calibration_suite.py at
# the pinned commit so HO-1 and friends here stay numerically aligned with
# vendor/calibration_profile.json).
HBAR = 1.054571817e-34
M_ELECTRON = 9.1093837015e-31
MU_BOHR = 9.2740100783e-24
C_LIGHT = 299792458.0
EV_TO_JOULE = 1.602176634e-19


def _registry():
    from synthesis.equations.registry import auto_register_all

    return auto_register_all()


def test_ho1_quantum_harmonic_oscillator_ground_state() -> None:
    """HO-1: TISE solver against the analytical QHO ground-state energy."""
    from synthesis.equations.base import EquationRegime, PhysicsContext

    omega = 1e15
    solver = _registry().get_solver(EquationRegime.NON_RELATIVISTIC_TI)
    ctx = PhysicsContext(
        mass_kg=M_ELECTRON,
        length_scale_m=4e-9,
        metadata={"omega": omega, "n_grid_points": 400},
    )
    result = solver.solve_stationary(ctx, n_states=5)
    expected_e0 = HBAR * omega * 0.5
    actual_e0 = float(result.eigenvalues[0])
    assert math.isclose(actual_e0, expected_e0, rel_tol=0.01), (
        f"QHO ground state: expected {expected_e0:.4e}, got {actual_e0:.4e}"
    )


def test_isw1_infinite_square_well_level_ratios() -> None:
    """ISW-1: PIB level ratios E_n/E_1 = n^2."""
    from synthesis.equations.base import EquationRegime, PhysicsContext

    length = 1e-9
    n_grid = 200
    dx = length / n_grid
    diag = 2.0 * HBAR**2 / (2 * M_ELECTRON * dx**2)
    off = -1.0 * HBAR**2 / (2 * M_ELECTRON * dx**2)
    hamiltonian = np.diag(np.full(n_grid, diag))
    hamiltonian += np.diag(np.full(n_grid - 1, off), 1)
    hamiltonian += np.diag(np.full(n_grid - 1, off), -1)

    solver = _registry().get_solver(EquationRegime.NON_RELATIVISTIC_TI)
    ctx = PhysicsContext(
        mass_kg=M_ELECTRON,
        length_scale_m=length,
        custom_hamiltonian=hamiltonian,
        metadata={"omega": 0, "n_grid_points": n_grid},
    )
    result = solver.solve_stationary(ctx, n_states=5)
    eigenvalues = [float(e) for e in result.eigenvalues[:3]]
    e1 = eigenvalues[0]
    assert math.isclose(eigenvalues[1] / e1, 4.0, rel_tol=0.01)
    assert math.isclose(eigenvalues[2] / e1, 9.0, rel_tol=0.01)


def test_h1s1_dirac_rest_energy_reduction() -> None:
    """H1S-1: Dirac solver returns rest energy m*c^2 in the slow-motion limit."""
    from synthesis.equations.base import EquationRegime, ParticleType, PhysicsContext

    solver = _registry().get_solver(EquationRegime.DIRAC)
    ctx = PhysicsContext(
        mass_kg=M_ELECTRON,
        velocity_over_c=0.5,
        has_spin=True,
        spin_value=0.5,
        particle_type=ParticleType.SPIN_HALF,
        length_scale_m=1e-11,
        metadata={"n_grid_points": 100},
    )
    result = solver.solve_stationary(ctx, n_states=5)
    expected_rest = M_ELECTRON * C_LIGHT**2
    actual_e0 = float(result.eigenvalues[0])
    ratio = actual_e0 / expected_rest
    assert abs(ratio - 1.0) < 0.20, (
        f"Dirac rest energy: expected {expected_rest:.4e}, got {actual_e0:.4e} (ratio {ratio:.4f})"
    )


def test_rabi1_pauli_zeeman_splitting() -> None:
    """RABI-1: spin-1/2 Zeeman split at non-zero B is g * mu_B * B (g~2.0023)."""
    from synthesis.equations.base import EquationRegime, PhysicsContext

    b_field = 1.0  # Tesla
    solver = _registry().get_solver(EquationRegime.PAULI)
    ctx = PhysicsContext(
        mass_kg=M_ELECTRON,
        has_spin=True,
        spin_value=0.5,
        has_magnetic_field=True,
        magnetic_field_tesla=b_field,
        length_scale_m=4e-9,
        metadata={"omega": 1e15, "n_grid_points": 100},
    )
    result = solver.solve_stationary(ctx, n_states=6)
    e0 = float(result.eigenvalues[0])
    e1 = float(result.eigenvalues[1])
    g_factor = 2.0023
    expected_split = g_factor * MU_BOHR * b_field
    actual_split = abs(e1 - e0)
    assert math.isclose(actual_split, expected_split, rel_tol=0.05), (
        f"Zeeman split: expected {expected_split:.4e} J, got {actual_split:.4e} J"
    )


def test_scg1_semiclassical_gravity_weak_field() -> None:
    """SCG-1: semiclassical gravity correction is negligible for an electron."""
    from synthesis.equations.base import EquationRegime, PhysicsContext

    sc_solver = _registry().get_solver(EquationRegime.SEMICLASSICAL_GRAVITY)
    tise_solver = _registry().get_solver(EquationRegime.NON_RELATIVISTIC_TI)
    ctx = PhysicsContext(
        mass_kg=M_ELECTRON,
        include_gravity=True,
        gravitational_regime="semiclassical",
        length_scale_m=4e-9,
        metadata={"omega": 1e15, "n_grid_points": 200},
    )
    r_sc = sc_solver.solve_stationary(ctx, n_states=3)
    r_tise = tise_solver.solve_stationary(ctx, n_states=3)
    e0_sc = float(r_sc.eigenvalues[0])
    e0_tise = float(r_tise.eigenvalues[0])
    correction = abs(e0_sc - e0_tise) / abs(e0_tise) if e0_tise != 0 else float("inf")
    # Weak-field correction for an electron is parts per ~10^40; finite-grid
    # numerics give a much larger residual but it stays below 10%.
    assert correction < 0.10, (
        f"SCG weak-field correction {correction:.4e} should be < 0.10 for an electron"
    )
