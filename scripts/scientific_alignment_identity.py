"""Narrow alignment role gate; no chain-order or sequence-based authority."""


def validate_scientific_revision(revision):
    if revision is not None and (type(revision) is not int or revision != 1):
        raise ValueError('Unsupported core protein scientific contract revision')


def validate_scientific_roles(ref_chain_ids, mobile_structure, binder, target, *, chain_map=None):
    """Validate all roles before mutating; remaps are explicit producer/request input.

    Chain maps are complete source-chain -> output-chain bijections. Identity
    mappings need not be supplied when source chains already match roles.
    """
    roles = list(binder) + list(target)
    if not binder or not target or any(not isinstance(c, str) or not c for c in roles) or len(set(roles)) != len(roles):
        raise ValueError('Missing, duplicate, or overlapping binder/target roles')
    ref_ids = list(ref_chain_ids)
    models = list(mobile_structure.get_models())
    if len(models) != 1 or len(set(ref_ids)) != len(ref_ids):
        raise ValueError('Ambiguous model/chain instances for roles')
    model = models[0]
    chains = list(model.get_chains())
    ids = [c.id for c in chains]
    mapping = {c: c for c in ids} if chain_map is None else chain_map
    if not isinstance(mapping, dict) or set(mapping) != set(ids) or any(not isinstance(c, str) or not c for c in mapping.values()) or len(set(mapping.values())) != len(ids):
        raise ValueError('Missing or ambiguous explicit chain map')
    required = set(roles)
    if not required <= set(ref_ids) or not required <= set(mapping.values()):
        raise ValueError('Binder/target roles missing after explicit chain map; no shared-chain fallback')
    # Detach before assigning to permit swaps without Biopython dictionary collisions.
    for chain in chains:
        model.detach_child(chain.id)
    for chain, source_id in zip(chains, ids):
        chain.id = mapping[source_id]
        model.add(chain)
    if not required <= {c.id for c in model}:
        raise ValueError('Binder/target roles invalid after remap')
