"""Pure financial domain.

This package must never import a web framework, an ORM, an AI SDK, an HTTP
client or any outer layer. The rule is enforced by import-linter contracts in
pyproject.toml and verified by tests/unit/test_architecture.py.
"""
