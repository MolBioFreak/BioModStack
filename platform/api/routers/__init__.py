"""API routers are imported explicitly by their owning runtime entrypoint.

Do not eagerly import optional product modules when the local NGS/MolBio API
or workflow adapter starts.
"""
