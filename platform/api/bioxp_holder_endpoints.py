"""Named holder identity shared by Transfer and Move authoring; no coordinates."""
HOLDER_TARGETS = [f'LOC_TROUGH_{kind}_ROW_{row}{suffix}' for kind in ('MICRO', 'PCR') for row in (1, 2)
                  for suffix in ([''] if kind == 'MICRO' else [''] + [f'_WELL_{well}' for well in range(1, 9)])]


def holder_row(target):
    if target not in HOLDER_TARGETS:
        raise ValueError('target: expected an exact Bulk reagent holder target')
    return target.removeprefix('LOC_TROUGH_').split('_WELL_')[0].lower()
