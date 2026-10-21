"""Optional per-native-sample Shape evidence using the global FA reducers."""
import hashlib
from pathlib import Path
from analyse_fampnn import compute_psce_profile, psce_policy, predicted_sidechain_summary
from analyse_fampnn_seq_probs import analyze_sample_pkl


def analyze_sample(runtime_dir, sample_id, settings):
    root = Path(runtime_dir)
    structure = root / 'samples' / f'{sample_id}.pdb'
    sample = root / 'sample_pkls' / f'{sample_id}.pkl'
    evidence = {'native_sample_id': sample_id, 'psce_available': False,
                'seq_probs': {'fampnn_seq_probs_available': False, 'missing': ['sample_pkl']}}
    if structure.is_file():
        evidence['native_structure'] = {'filename': f'samples/{structure.name}',
                                       'sha256': hashlib.sha256(structure.read_bytes()).hexdigest()}
        try:
            profile = compute_psce_profile(structure, psce_policy('all_chains', True))
            evidence.update(psce_available=True, psce=profile['summary'], psce_policy=profile['policy'],
                            psce_population='all_emitted_sidechains_including_preserved_or_unpredicted_zeros',
                            predicted_sidechain_psce=predicted_sidechain_summary(structure, profile))
        except (ValueError, OSError) as exc:
            evidence['psce_unavailable_reason'] = str(exc)
    if sample.is_file():
        try:
            evidence['seq_probs'] = analyze_sample_pkl(sample,
                mutation_top_n=settings.get('fampnn_mutation_top_n', 25),
                mutation_min_log_odds_delta=settings.get('fampnn_mutation_min_log_odds_delta', 0.0))
        except (ValueError, OSError, TypeError, EOFError) as exc:
            evidence['seq_probs']['error'] = str(exc)
    # Only authored filtering changes selection. Missing optional evidence does
    # not become a new success criterion or fabricated high-error score.
    evidence['passes_psce_filter'] = True
    if settings.get('enable_fampnn_filter') is not False and evidence['psce_available']:
        for setting, metric in [('fampnn_max_psce', 'avg_psce'), ('fampnn_max_residue_psce', 'max_psce')]:
            threshold = settings.get(setting)
            if threshold is not None and evidence['psce'][metric] > threshold:
                evidence['passes_psce_filter'] = False
    return evidence
