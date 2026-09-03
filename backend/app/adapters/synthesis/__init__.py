"""The Claude synthesis adapter (Phase 7B).

The only place in the synthesis slice where a provider SDK appears. An import
contract enforces that, so `anthropic` cannot drift into the domain, the
application layer or a port.
"""

# Deliberately no package-level re-exports - see app/domain/synthesis/__init__.py
# for the Phase 6 import cycle this avoids.
