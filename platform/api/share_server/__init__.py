"""Isolated public share server for frozen MolBio share bundles.

This package must stay importable without BMS database, session, routing or
workflow modules. ``grants`` is pure stdlib and is shared with the BMS API's
share router; ``app`` is the standalone ASGI application served via Funnel.
"""
