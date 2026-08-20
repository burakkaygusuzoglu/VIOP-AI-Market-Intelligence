"""Adapters implement ports using concrete infrastructure.

Adapters may depend on the application and domain layers. They must never be
imported by the API route or schema modules; wiring happens only in the
composition root (app/main.py and app/api/dependencies.py).
"""
