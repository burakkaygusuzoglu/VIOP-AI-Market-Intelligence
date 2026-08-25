"""Screenshot intake, decode verification and the vision contract (§38, §68, §98).

The application-layer half of Phase 6: it turns untrusted bytes into a verified
`AcceptedScreenshot`, untrusted model JSON into domain values, and a set of
screenshots into a reviewed set with every mismatch made explicit.

Both directions treat their input as hostile. Uploads pass four layers - size
bound, header preflight, dimension policy and a real bounded decode - before
any byte leaves the process. Model responses must satisfy a strict Pydantic
schema with `extra="forbid"` and are never repaired.

No SDK is imported here. The Anthropic client lives in `app.adapters.vision`.
"""

# Deliberately no package-level re-exports.
#
# This module used to re-export the whole subpackage as a convenience facade.
# It made the application impossible to start: `app.application.ports.screenshot`
# imports `AcceptedScreenshot` from `.intake`, and importing any submodule runs
# this file first, which imported `.analysis`, which imports the port that is
# still half-initialised. `uvicorn app.main:app` failed on the first import.
#
# The test suite hid it, because a test that imports the adapter first warms
# the package in a lucky order. Importing submodules directly - which every
# caller already did - removes the cycle rather than sequencing around it.
