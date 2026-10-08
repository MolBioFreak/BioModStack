## Variant: Compact Registry

### Design stance

Start with a low-complexity Project table and open Project details only when selected.

### Key choices

- Layout: full-width Project registry with an optional right-side detail drawer.
- Density: compact and table-first.
- Primary interaction: search, filter, then open a Project drawer.
- Universal action: one `Add to Project` form shown beside common BMS source surfaces.
- Result behavior: the drawer organizes records while canonical viewers retain scientific rendering.

### Trade-offs

- Strong at: quick delivery and scanning many Projects.
- Weak at: sustained work inside one complex Project because detail is placed in a drawer.

### Best for

- The smallest viable first release.
- A Project count that grows faster than Project-detail complexity.
