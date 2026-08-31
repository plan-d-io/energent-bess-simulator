"""Documented optimizer tolerances.

MODEL_SPEC.md requires a stated numerical allowance when a later objective
keeps an earlier result unchanged, but it does not name the values. Using the
0.001 kWh Fluvius measurement tolerance as that allowance would let a later
step legally give away 1 Wh of an earlier energy result, so the sequential
solves use solver-scale slack instead. Both values are stored in summary
metadata and are not featured in user-facing reports.
"""

from btm_sim.fluvius.constants import DOCUMENTED_TOLERANCE_KWH, INTERVAL_HOURS

# Allowed numerical change in an earlier result when applying the next objective.
LEXICO_TOL_KWH = 1e-9
LEXICO_TOL_KW = 1e-9 / INTERVAL_HOURS
LEXICO_TOL_EUR = 1e-9
# Post-solve physics checks still use the Fluvius documented energy tolerance.
POSTCHECK_TOL_KWH = DOCUMENTED_TOLERANCE_KWH
