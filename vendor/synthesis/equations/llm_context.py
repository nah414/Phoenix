"""
Dr. Frank & Eddy -- Equation Family Context for Local LLMs

Generates context strings that teach the Qwen3 local LLM
about available equation regimes, classification thresholds, and
decision rules. Injected into system prompts so the LLMs can:
  - Understand which equation was selected and why
  - Write proper physics contracts with equation reasoning
  - Make informed decisions during the wobble/convergence loop
"""

import re
from typing import Optional

from .base import EquationRegime, PhysicsContext, ParticleType


EQUATION_FAMILY_CONTEXT = """
## Quantum Equation Family -- Solver Selection Rules

The quantum engine auto-selects the correct Schrodinger equation form
based on the physical system. The universal principle is always valid:
ihbar d_psi/dt = H psi. What changes is the Hamiltonian and state space.

### Decision Tree (ordered by priority)
1. Gravity quantum? -> Wheeler-DeWitt (H|Psi>=0, no time, minisuperspace)
2. Gravity semiclassical? -> Semiclassical gravity SE (<T_mu_nu> sources curvature)
3. Gravity Newtonian? -> Add V=-GMm/r to Hamiltonian
4. v/c > 0.1? -> Dirac (spin-1/2, 4-component spinor) or Klein-Gordon (spin-0)
5. v/c > 0.01? -> Breit-Pauli corrections (mass-velocity, Darwin, spin-orbit)
6. Spin-1/2 + B field? -> Pauli equation (Zeeman + spin-orbit coupling)
7. Open system (decoherence)? -> Stochastic SE (Monte Carlo wave function)
8. Classical-quantum bridge? -> Ehrenfest (break time) or WKB (semiclassical)
9. Default -> Standard TDSE (split-step Fourier) or TISE (eigenvalue problem)

### Thresholds
- Relativistic: v/c > 0.01 triggers corrections, v/c > 0.1 requires Dirac/KG
- Fine structure: alpha = 1/137.036 -- corrections scale as (Z*alpha)^2
- Gravitational decoherence: matters for mass > 10^-17 kg (10^10 amu)
- Stochastic SE: for Markovian/weak environment coupling
- WKB validity: |d_lambda/dx| << 1 (breaks at turning points)

### Available Regimes (14 total)
NON_RELATIVISTIC_TI: H psi = E psi (eigenvalue problem)
NON_RELATIVISTIC_TD: ihbar d_psi/dt = H psi (time evolution)
PAULI: spin-1/2 in EM fields (Zeeman + spin-orbit)
BREIT_PAULI: fine-structure corrections (mass-velocity, Darwin)
DIRAC: fully relativistic 4-component spinor
KLEIN_GORDON: relativistic spin-0 bosons (second order in time)
STOCHASTIC_SE: Monte Carlo wave function (open systems)
EHRENFEST: classical-quantum bridge (detects quantum break time)
WKB: semiclassical wave function construction
GRAVITATIONAL_DECOHERENCE: Diosi-Penrose model (theoretical)
SEMICLASSICAL_GRAVITY: <T_mu_nu> -> Einstein -> metric -> H
WHEELER_DEWITT: H|Psi>=0, quantum cosmology (frontier physics)
LINDBLAD: open system density matrix (via existing TJM)
REDFIELD: weak-coupling bath (planned)

### Physics Contract Requirements
Every simulation result MUST include:
- regime_used: which equation was selected
- classification_confidence: how confident the classifier is (0-1)
- classification_reasoning: human-readable explanation
- frontier_physics: True if using Wheeler-DeWitt or unconfirmed models
- model_status: "confirmed" | "theoretical_unconfirmed" | "research_grade"

### Memory Estimates (governor pre-flight)
- TDSE: N^2 * 16 bytes (N = grid points)
- Pauli: (2N)^2 * 16 bytes (2x for spin-up/down)
- Dirac: (4N)^2 * 16 bytes (4x for 4-component spinor)
- Klein-Gordon: (2N)^2 * 16 bytes (first-order system)
- Wheeler-DeWitt 2D: (N_alpha * N_phi)^2 * 16 bytes (can be huge)

### Additional Capabilities
- Circuit Library: QFT, Grover, Bell, GHZ, W state, teleportation (synthesis.quantum.circuits)
- DPD Engine: Drive-Probe-Drive timing-aware quantum control (synthesis.core)
- Hardware Backends: Superconducting, Trapped Ion, NMR, Telecom Photonic
- Lindblad RK4: Full density matrix evolution with decoherence (synthesis.core.lindblad_rk4)
- Co-authors can invoke simulate_physics() for mathematical verification
""".strip()


def get_equation_context() -> str:
    """Return the equation family context string for LLM system prompts."""
    return EQUATION_FAMILY_CONTEXT


def extract_physics_context_from_text(text: str) -> Optional[PhysicsContext]:
    """Extract physics context from natural language text.

    Strategy: Keyword extraction + LLM enhancement (default-on).
    1. Try keyword extraction first (fast, reliable for explicit physics terms)
    2. If keywords found, use LLM to refine parameter values
    3. If no keywords found, ask LLM if this is a physics problem at all

    The LLM call is optional -- if the local brain is unavailable,
    keyword extraction alone is used (preserving current behavior).
    """
    import logging
    log = logging.getLogger(__name__)

    # Step 1: Keyword extraction (existing logic, preserved as fallback)
    ctx_from_keywords = _keyword_extract(text)

    # Step 2: Try LLM enhancement if local brain is available
    try:
        # v6.4.3 Phase 5 close (T-036): use the shared orchestrator
        # singleton instead of ``LocalBrainOrchestrator()`` so this
        # call doesn't spawn its own Qwen3 daemon. Pre-fix, a single
        # /api/query/stream produced two daemon starts from this
        # function alone (quantum_preclassify + equation_classify
        # both route through here, each getting its own orchestrator).
        from local_brain import get_shared_orchestrator
        brain = get_shared_orchestrator()

        prompt = f"""Analyze this text and determine if it describes a quantum physics problem.
If yes, extract these parameters (return JSON only, no explanation):
{{
  "is_physics": true,
  "velocity_over_c": 0.0,
  "has_spin": false,
  "spin_value": 0.0,
  "has_magnetic_field": false,
  "magnetic_field_tesla": 0.0,
  "is_open_system": false,
  "include_gravity": false,
  "gravitational_regime": "none",
  "is_time_dependent": false,
  "particle_type": "point_particle"
}}
If NOT a physics problem, return: {{"is_physics": false}}

Text: {text[:500]}"""

        # v6.4.3 Phase 3 (T-035): the routing prompt above is ~250-350 tokens
        # depending on the input text window. Qwen3 NPU daemon treats
        # max_length as input+output combined, so max_length=200 was shorter
        # than the prompt itself -- every call overflowed with
        # "input_ids size (N) exceeds max length (200)", the daemon got
        # killed and restarted, retried, failed identically, and the whole
        # routing attempt was wasted (~10 s of lost time per query).
        # Raising to 2048 matches the daemon's own default and leaves plenty
        # of headroom for the short JSON reply we actually need.
        response = brain.generate(prompt, mode="routing", max_length=2048)
        answer = response.get("answer", "") if isinstance(response, dict) else str(response)

        # Parse JSON from response
        import json
        # Find JSON in response (may have surrounding text)
        json_start = answer.find('{')
        json_end = answer.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            params = json.loads(answer[json_start:json_end])
        else:
            log.debug("LLM did not return JSON, using keyword result")
            return ctx_from_keywords

        if not params.get("is_physics", False):
            return ctx_from_keywords  # Defer to keyword result

        # Build PhysicsContext from LLM-extracted parameters
        ctx = PhysicsContext(
            velocity_over_c=params.get("velocity_over_c", 0.0),
            has_spin=params.get("has_spin", False),
            spin_value=params.get("spin_value", 0.0),
            has_magnetic_field=params.get("has_magnetic_field", False),
            magnetic_field_tesla=params.get("magnetic_field_tesla", 0.0),
            is_open_system=params.get("is_open_system", False),
            include_gravity=params.get("include_gravity", False),
            gravitational_regime=params.get("gravitational_regime", "none"),
        )

        if params.get("particle_type") == "spin_half":
            ctx.particle_type = ParticleType.SPIN_HALF
            ctx.has_spin = True
            ctx.spin_value = 0.5
        elif params.get("particle_type") == "spin_zero_boson":
            ctx.particle_type = ParticleType.SPIN_ZERO_BOSON
        elif params.get("particle_type") == "geometry":
            ctx.particle_type = ParticleType.GEOMETRY

        # Merge: LLM result takes precedence, but keep keyword detections
        # that the LLM might have missed
        if ctx_from_keywords is not None:
            if ctx_from_keywords.velocity_over_c > ctx.velocity_over_c:
                ctx.velocity_over_c = ctx_from_keywords.velocity_over_c
            if ctx_from_keywords.include_fine_structure:
                ctx.include_fine_structure = True
                ctx.include_spin_orbit = True
            if ctx_from_keywords.metadata.get("classical_quantum_bridge"):
                ctx.metadata["classical_quantum_bridge"] = True
            if ctx_from_keywords.metadata.get("wkb_requested"):
                ctx.metadata["wkb_requested"] = True

        return ctx

    except Exception as e:
        log.debug("LLM extraction unavailable, using keywords: %s", e)
        return ctx_from_keywords


def _keyword_extract(text: str) -> Optional[PhysicsContext]:
    """Extract a PhysicsContext from co-author response text via keyword detection.

    Returns None if the text doesn't describe a physics system.
    """
    t = text.lower()

    # Quick relevance check -- must mention at least one physics keyword
    physics_signals = [
        "quantum", "schrodinger", "hamiltonian", "wave function", "wavefunction",
        "electron", "photon", "qubit", "spin", "energy level", "eigenvalue",
        "relativistic", "dirac", "klein-gordon", "magnetic field", "boson",
        "fermion", "decoherence", "entanglement", "superposition", "gravity",
        "cosmolog", "planck", "hydrogen", "atom", "particle", "potential",
        "harmonic oscillator", "tunneling", "nuclear", "angular momentum",
    ]
    if not any(kw in t for kw in physics_signals):
        return None

    ctx = PhysicsContext()

    # Detect relativistic regime
    if any(kw in t for kw in ["relativistic", "dirac equation", "dirac spinor",
                               "speed of light", "lorentz", "antimatter", "positron"]):
        ctx.velocity_over_c = 0.5
    elif any(kw in t for kw in ["fine structure", "breit-pauli", "spin-orbit coupling",
                                 "darwin term", "mass-velocity"]):
        ctx.velocity_over_c = 0.05
        ctx.include_fine_structure = True
        ctx.include_spin_orbit = True

    # Detect spin
    if any(kw in t for kw in ["spin", "spinor", "pauli", "zeeman", "magnetic moment",
                               "fermion", "electron", "qubit"]):
        ctx.has_spin = True
        ctx.spin_value = 0.5
        ctx.particle_type = ParticleType.SPIN_HALF

    # Detect bosonic
    if any(kw in t for kw in ["boson", "photon", "higgs", "pion", "klein-gordon",
                               "spin-0", "scalar field"]):
        ctx.particle_type = ParticleType.SPIN_ZERO_BOSON
        ctx.spin_value = 0.0

    # Detect magnetic field
    if any(kw in t for kw in ["magnetic field", "zeeman", "b field", "tesla",
                               "magnetization", "larmor"]):
        ctx.has_magnetic_field = True
        ctx.magnetic_field_tesla = 1.0

    # Detect open system / decoherence
    if any(kw in t for kw in ["decoherence", "open system", "dissipation", "lindblad",
                               "environment", "bath", "noise", "dephasing"]):
        ctx.is_open_system = True
        ctx.environment_coupling = "markovian"

    # Detect gravity
    if any(kw in t for kw in ["wheeler-dewitt", "quantum cosmolog", "quantum gravity",
                               "minisuperspace", "wave function of the universe"]):
        ctx.include_gravity = True
        ctx.gravitational_regime = "quantum"
        ctx.particle_type = ParticleType.GEOMETRY
    elif any(kw in t for kw in ["semiclassical gravity", "curved spacetime",
                                 "expectation value.*metric"]):
        ctx.include_gravity = True
        ctx.gravitational_regime = "semiclassical"
    elif any(kw in t for kw in ["gravitational decoherence", "penrose", "diosi"]):
        ctx.include_gravity = True
        ctx.gravitational_regime = "newtonian"
        ctx.mass_kg = 1e-15

    # Detect classical-quantum bridge
    if any(kw in t for kw in ["ehrenfest", "correspondence principle",
                               "classical limit", "break time"]):
        ctx.metadata["classical_quantum_bridge"] = True

    if any(kw in t for kw in ["wkb", "semiclassical approximation",
                               "turning point", "tunneling probability"]):
        ctx.metadata["wkb_requested"] = True

    # Detect stochastic / Monte Carlo
    if any(kw in t for kw in ["monte carlo", "stochastic", "quantum trajectory",
                               "quantum jump"]):
        ctx.is_open_system = True
        ctx.environment_coupling = "markovian"
        ctx.metadata["n_trajectories"] = 50

    # Extract mass hints
    mass_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|kilogram)", t)
    if mass_match:
        ctx.mass_kg = float(mass_match.group(1))

    energy_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:eV|electron.?volt)", t)
    if energy_match:
        ctx.energy_eV = float(energy_match.group(1))

    return ctx


def get_regime_description(regime: EquationRegime) -> str:
    """Return a short description of a specific regime for LLM context."""
    descriptions = {
        EquationRegime.NON_RELATIVISTIC_TI: "Time-independent Schrodinger: H psi = E psi (eigenvalue problem)",
        EquationRegime.NON_RELATIVISTIC_TD: "Time-dependent Schrodinger: ihbar d_psi/dt = H psi (standard workhorse)",
        EquationRegime.PAULI: "Pauli equation: spin-1/2 in EM fields with Zeeman and spin-orbit coupling",
        EquationRegime.BREIT_PAULI: "Breit-Pauli: fine-structure corrections (mass-velocity, Darwin, spin-orbit)",
        EquationRegime.DIRAC: "Dirac equation: fully relativistic 4-component spinor for v/c > 0.1",
        EquationRegime.KLEIN_GORDON: "Klein-Gordon: relativistic spin-0 bosons (second order in time)",
        EquationRegime.STOCHASTIC_SE: "Stochastic SE: Monte Carlo wave function for open systems",
        EquationRegime.EHRENFEST: "Ehrenfest bridge: tracks classical-quantum correspondence and break time",
        EquationRegime.WKB: "WKB: semiclassical approximation, constructs quantum state from classical action",
        EquationRegime.GRAVITATIONAL_DECOHERENCE: "Diosi-Penrose: gravity-induced decoherence (theoretical)",
        EquationRegime.SEMICLASSICAL_GRAVITY: "Semiclassical gravity: quantum matter on curved spacetime",
        EquationRegime.WHEELER_DEWITT: "Wheeler-DeWitt: H|Psi>=0, quantum cosmology (frontier, no time)",
        EquationRegime.LINDBLAD: "Lindblad master equation: open system density matrix evolution",
        EquationRegime.REDFIELD: "Redfield equation: weak-coupling bath (planned)",
    }
    return descriptions.get(regime, f"Unknown regime: {regime.name}")
