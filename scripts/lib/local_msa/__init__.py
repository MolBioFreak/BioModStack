"""Local MSA package surface.

Keep package import side effects minimal so legacy scripts can import individual
submodules during the staged extraction without triggering circular imports.
"""

__all__ = ["batching", "runtime"]
