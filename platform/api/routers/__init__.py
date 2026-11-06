"""Routers package.

Submodules are imported by their owners (main.py, workflow_adapter_app.py).
No eager imports here: the workflow adapter needs only two routers, and
importing every router made each adapter start load the whole API.
"""
