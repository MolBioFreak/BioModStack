"""Pinned seq_design-only instrumentation: no sampling/RNG operations change.

The caller verifies installed source hashes before applying these transformations.
Only unused trajectory retention is removed; sequence/coordinate/error tensors,
order, sampling method and model calls are unchanged. CPU feature reuse returns
independent copies, preserving native mutation ownership between repeated inputs.
"""
import copy
import hashlib
import json
from pathlib import Path


def cached_features(cache, loader, path):
    key = str(path)
    if key not in cache:
        # np.repeat emits each input's samples contiguously, including across
        # batch boundaries. Keep one input, not an unbounded protein collection.
        cache.clear()
        cache[key] = loader(path)
    return copy.deepcopy(cache[key])


def write_psce_scope(samples, paths):
    """Optional evidence from the exact native writer batch, not score != 0."""
    import warnings
    try:
        from fampnn.data.protein import PDB_CHAIN_IDS
        for i, path in enumerate(paths):
            def array(key):
                return samples[key][i].detach().cpu().numpy()
            present = array('seq_mask')
            predicted = array('bms_psce_observed_mask')
            chains, residues = array('chain_index'), array('residue_index')
            rows = [dict(chain_id=PDB_CHAIN_IDS[int(chains[j])], author_number=int(residues[j]),
                         insertion_code='', predicted=bool(predicted[j]))
                    for j in range(len(present)) if present[j]]
            Path(str(path) + '.psce_scope').write_text(json.dumps(dict(
                version=1, structure_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                population='native_predicted_sidechains', residues=rows)) + '\n')
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        warnings.warn(f'FA-MPNN optional pSCE scope unavailable: {exc}')


def instrument(relative, text):
    from maturation_native_adapter import _replace
    if relative == 'fampnn/inference/seq_design.py':
        text = _replace(text, '    pbar = tqdm(total=len(pdb_files_repeated))',
                        '    bms_feature_cache = {}\n    pbar = tqdm(total=len(pdb_files_repeated))')
        text = _replace(text, '            data = load_feats_from_pdb(pdb_file)',
                        '            data = _bms_fa_runtime.cached_features(bms_feature_cache, load_feats_from_pdb, pdb_file)')
        text = _replace(text, '            "psce": aux["psce"],',
                        '            "bms_psce_observed_mask": aux["bms_psce_observed_mask"],\n            "psce": aux["psce"],')
        text = _replace(text, '        # Save samples as pkl',
                        '        _bms_fa_runtime.write_psce_scope(samples, pdbs)\n        # Save samples as pkl')
    elif relative == 'fampnn/model/sd_model.py':
        # Limit changes to sample(), not sidechain_pack or other public methods.
        start = text.index('    def sample(')
        end = text.index('    @staticmethod', start)
        before, body, after = text[:start], text[start:end], text[end:]
        names = ('xt_traj', 'aatype_t_traj', 'aatype_pred_traj', 'psce_t_traj', 'seq_logits_traj')
        body = '\n'.join(line for line in body.split('\n')
                         if not any(f'{name}.append(' in line or f'aux["{name}"] = torch.stack(' in line
                                    for name in names))
        body = _replace(body, '        aux["psce"] = psce_t',
                        '        aux["bms_psce_observed_mask"] = (scn_mlm_mask > 0) & (scn_override_mask == 0) & (seq_mask > 0)\n        aux["psce"] = psce_t')
        text = before + body + after
    return 'import fampnn_native_runtime as _bms_fa_runtime\n' + text
