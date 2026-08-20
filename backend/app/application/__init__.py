"""Application layer.

Depends on the domain and on its own ports. It must never import FastAPI,
SQLAlchemy, an AI SDK, an HTTP client, an adapter or the API layer.
"""
