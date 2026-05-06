"""
Dr. Frank & Eddy -- Schrodinger Equation Family: Abstract Base

Every form of the Schrodinger equation implements this interface.
The registry uses it to dispatch computations to the correct solver.

The universal principle: ihbar d_psi/dt = H_hat psi is always valid.
What changes is:
  1. The Hamiltonian H (what physics is included)
  2. The nature of psi (scalar, spinor, field operator, functional)
  3. The Hilbert space psi lives in (single-particle, Fock space, superspace)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Dict, Any, List, Tuple
import numpy as np


class EquationRegime(Enum):
    """Which branch of the Schrodinger family this solver belongs to."""
    NON_RELATIVISTIC_TI = auto()    # Time-independent: H psi = E psi
    NON_RELATIVISTIC_TD = auto()    # Time-dependent: ihbar d_psi/dt = H psi
    PAULI = auto()                   # Spin-1/2 in EM field
    BREIT_PAULI = auto()             # Fine-structure corrections
    DIRAC = auto()                   # Fully relativistic spin-1/2
    KLEIN_GORDON = auto()            # Relativistic spin-0
    LINDBLAD = auto()                # Open system density matrix
    STOCHASTIC_SE = auto()           # Monte Carlo wave function
    REDFIELD = auto()                # Weak-coupling bath
    EHRENFEST = auto()               # Classical <-> quantum bridge
    WKB = auto()                     # Semiclassical approximation
    GRAVITATIONAL_DECOHERENCE = auto()
    SEMICLASSICAL_GRAVITY = auto()
    WHEELER_DEWITT = auto()          # H|Psi> = 0 (quantum cosmology)
    # v6.7.5 chunk 5 (BUILD_DEBT v68-D3): circuit synthesis is NOT a
    # PDE regime -- it has no solver in the equation registry. The
    # value exists so pre_classify_query can short-circuit on circuit
    # prompts (Grover's, Shor's, QFT, Bell, etc.) and skip the
    # mis-applied Schrodinger pre-simulation. pre_run_simulation
    # returns None for this regime because physics_context is None
    # by construction.
    QUANTUM_CIRCUIT = auto()


class ParticleType(Enum):
    """What kind of quantum entity we're simulating."""
    POINT_PARTICLE = auto()     # Standard non-relativistic
    SPIN_HALF = auto()          # Electron, transmon qubit in B field
    SPIN_ZERO_BOSON = auto()    # Photon (scalar approx), Higgs
    COMPOSITE = auto()          # Multi-particle system
    FIELD = auto()              # Quantum field (second quantized)
    GEOMETRY = auto()           # Spacetime geometry (Wheeler-DeWitt)


@dataclass
class PhysicsContext:
    """
    Describes the physical system being simulated.
    The classifier reads this to select the correct equation.
    """
    n_particles: int = 1
    particle_type: ParticleType = ParticleType.POINT_PARTICLE
    has_spin: bool = False
    spin_value: float = 0.0
    has_magnetic_field: bool = False
    magnetic_field_tesla: float = 0.0
    has_electric_field: bool = False
    velocity_over_c: float = 0.0
    include_spin_orbit: bool = False
    include_fine_structure: bool = False
    is_open_system: bool = False
    environment_coupling: str = "none"
    include_gravity: bool = False
    gravitational_regime: str = "none"
    mass_kg: float = 0.0
    energy_eV: float = 0.0
    length_scale_m: float = 1e-9
    time_scale_s: float = 1e-9
    custom_hamiltonian: Optional[np.ndarray] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SolverResult:
    """Standardized result from any equation solver."""
    state: np.ndarray
    energy: Optional[float] = None
    eigenvalues: Optional[np.ndarray] = None
    eigenstates: Optional[np.ndarray] = None
    expectation_values: Dict[str, float] = field(default_factory=dict)
    time_points: Optional[np.ndarray] = None
    state_history: Optional[np.ndarray] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    regime_used: Optional[EquationRegime] = None
    solver_name: str = ""
    convergence_info: Dict[str, Any] = field(default_factory=dict)


class EquationSolver(ABC):
    """
    Abstract interface for all Schrodinger equation family solvers.

    Every solver must:
    1. Report which regime(s) it handles
    2. Validate that a PhysicsContext is compatible
    3. Build the appropriate Hamiltonian
    4. Evolve the state or find eigenstates
    """

    @abstractmethod
    def regime(self) -> EquationRegime:
        ...

    @abstractmethod
    def can_handle(self, ctx: PhysicsContext) -> Tuple[bool, float]:
        """Returns (can_handle, confidence 0.0-1.0). Higher = better match."""
        ...

    @abstractmethod
    def build_hamiltonian(self, ctx: PhysicsContext) -> np.ndarray:
        ...

    @abstractmethod
    def solve_stationary(self, ctx: PhysicsContext, n_states: int = 5) -> SolverResult:
        ...

    @abstractmethod
    def evolve(self, ctx: PhysicsContext, initial_state: np.ndarray,
               t_final: float, dt: float, store_history: bool = False) -> SolverResult:
        ...

    @abstractmethod
    def validate_state(self, state: np.ndarray, ctx: PhysicsContext) -> bool:
        ...

    def latex_representation(self) -> str:
        """Return the governing equation as a LaTeX string."""
        return r"i\hbar \frac{\partial}{\partial t} \Psi = \hat{H} \Psi"

    def estimated_memory_bytes(self, ctx: PhysicsContext) -> int:
        return 0

    def estimated_flops(self, ctx: PhysicsContext) -> int:
        return 0

    def gpu_beneficial(self, ctx: PhysicsContext) -> bool:
        return False
