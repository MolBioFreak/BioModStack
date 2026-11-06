from services.assembly.physical_ends import read_physical_ends, write_physical_ends
from services.assembly.types import FragmentEnd


def test_physical_ends_round_trip_and_digest_geometry_form():
    left = FragmentEnd(type="sticky_5", overhang="GATC", protruding_strand="top")
    right = FragmentEnd(type="blunt")
    written = write_physical_ends({"keep": 1}, left, right, "end_operation")
    assert written["keep"] == 1 and written["physical_ends"]["source"] == "end_operation"
    got_left, got_right = read_physical_ends(written)
    assert (got_left.type, got_left.overhang, got_left.protruding_strand) == ("sticky_5", "GATC", "top")
    assert got_right.type == "blunt" and got_right.overhang == ""

    digest = {"geometry": {
        "left_end": {"kind": "natural", "overhang_sequence_5to3": None, "protruding_strand": None},
        "right_end": {"kind": "three_prime_overhang", "overhang_sequence_5to3": "TGCA", "protruding_strand": "bottom"},
    }}
    a, b = read_physical_ends(digest)
    assert a.type == "blunt" and (b.type, b.overhang, b.protruding_strand) == ("sticky_3", "TGCA", "bottom")
    assert read_physical_ends({}) == (None, None) and read_physical_ends(None) == (None, None)
