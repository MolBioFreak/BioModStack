from services.assembly.dnaweaver_gibson import plan_vendor_gibson


def test_dnaweaver_plan_orders_preoverlapped_fragments_and_pydna_validates_target():
    target = ("ATGCGTACGATCGTACGCTAGCTAGCATCGATCG" * 30)[:900]

    plan = plan_vendor_gibson(
        target,
        circular=True,
        min_fragment_length=250,
        max_fragment_length=450,
        overlap_length=30,
    )

    assert plan.engine == "dnaweaver"
    assert plan.validator_engine == "pydna"
    assert len(plan.product.fragments) >= 2
    assert plan.product.circular is True
    assert plan.product.sequence == target
    assert len(plan.product.junctions) == len(plan.product.fragments)
    assert all(fragment.metadata["preparation"] == "ready_linear" for fragment in plan.product.fragments)
    assert plan.pydna_exact_candidate_count >= 1
