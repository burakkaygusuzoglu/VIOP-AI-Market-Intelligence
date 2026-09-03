"""The decision-safety core of Phase 7 (§7, §8, §6, §3).

Two things live here, both stdlib-only and both deterministic:

* `actions` - the four final states and the `ActionEnvelope` that says which of
  them deterministic policy permits. A synthesis model chooses inside a set it
  cannot widen.
* `references` - stable identifiers for every citable fact, plus the authority
  classes that keep a visual inference from reading as a measurement.

Nothing here calls a model, and nothing here recomputes a Phase 1-6 formula.
The envelope reads finished results and removes actions; it never re-derives
the findings it reads.
"""

# Deliberately no package-level re-exports.
#
# Phase 6 shipped a cycle through exactly this kind of convenience facade -
# `app.application.vision.__init__` re-exported a module that imported back
# into a half-initialised port, and `uvicorn app.main:app` failed with all six
# gates green. Submodules are imported directly here for the same reason:
# a facade that buys nothing is not worth the failure mode.
