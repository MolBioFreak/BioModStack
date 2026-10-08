"""Documentary picker suggestions only; never native validation or custody proof."""


def custody_suggestions():
    # Original BioXPCommonLib/ClassGlobals.cs lookupSrcDstPlate / lookupDesLocation
    # and plateName.cs; installed native oem_deck_movement translator retains these.
    objects = [
        ('PL_POOL', 'Pool plate', 'plate', 0),
        ('PL_OUTPUT', 'Output plate', 'plate', 1),
        ('PL_REAGENT', 'Reagent plate', 'plate', 2),
        ('CV_BIOSECURITY', 'Biosecurity cover', 'cover', 3),
        ('CV_OUTPUT', 'Output cover', 'cover', 4),
        ('CV_REAGENT', 'Reagent cover', 'cover', 5),
        ('STRIP1', 'Strip one', 'plate', 7),
        ('STRIP2', 'Strip two', 'plate', 8),
        ('STRIP3', 'Strip three', 'plate', 9),
        ('STRIP4', 'Strip four', 'plate', 10),
        ('TROUGH', 'Trough', 'plate', 11),
        ('PL_SYNTHESIS', 'Synthesis plate', 'plate', 12),
        ('PL_OLIGO_QUANT', 'Oligo quantitation plate', 'plate', 13),
        ('PL_GENE_QUANT', 'Gene quantitation plate', 'plate', 14),
        ('PL_ELUTION', 'Elution plate', 'plate', 16),
        ('PL_ACC', 'Accumulation plate', 'plate', 17),
        ('PL_TFF_REAGENT', 'TFF reagent block', 'plate', 19),
    ]
    # Only functionally applicable suggestions. The source translator also accepts
    # other location-name strings; those remain accessible as raw native options.
    # LOC_RC plate mapping is UNKNOWN (32), not the pipetting ordinal 3.
    destinations = [
        ('LOC_TC', 'Thermal cycler', 'LOC_TC', 23, 5),
        ('LOC_OC', 'Output chiller', 'LOC_OC', 21, 17),
        ('LOC_RC', 'Reagent cover position', 'LOC_RC', None, 19),
        ('LOC_BSCS', 'Biosecurity cover storage', 'LOC_BSCS', None, 4),
        ('LOC_RCS', 'Reagent cover storage', 'LOC_RC_COVER_STORAGE', None, 20),
        ('LOC_OCS', 'Output cover storage', 'LOC_OC_COVER_STORAGE', None, 18),
        ('LOC_MS', 'Magnetic station — plate placement (LOC_P_MS)', 'LOC_MS', 25, None),
    ]
    return {
        'objects': [dict(token=t, label=l, kind=k, ordinal=o) for t, l, k, o in objects],
        'destinations': [dict(token=t, label=l, station=s, plate_destination=p, cover_destination=c)
                         for t, l, s, p, c in destinations],
        'press_mode': {'token': 'PRESS', 'label': 'Press plate at placement (source PRESS mode)'},
        'source_anchor': 'Original BioXPCommonLib/ClassGlobals.cs lookupSrcDstPlate/lookupDesLocation and plateName.cs; native 4af441d30b0eb1f16aa7764f3fb234f6d4e0a69e src/bioxp/oem_deck_movement.py OEM_SCRIPT_PLATE_TOKENS/translate_oem_plate_move. Suggestions only: null means not offered, not refused. LOC_MS pipette ordinal 0 differs from LOC_P_MS plate destination 25; named labware is planned accounting, not physical custody evidence.',
    }
