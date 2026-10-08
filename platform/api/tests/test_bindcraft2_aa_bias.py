"""PR17 calculation-only backport; optional pinned-source/native CPU differential.

Set BMS_TEST_BC2_UPSTREAM to pristine d5bae16 and BMS_TEST_BC2_PYTHON
(or BMS_TEST_BC2_IMAGE) to the repaired runtime for native JAX checks.
No weights or GPU inference.
"""
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[3]
PATCH = ROOT / 'apptainer/bindcraft2-aa-bias.patch'
FILES = ('af2.py', 'campaign.py', 'protein_preparation.py', 'proteinmpnn.py',
         'sequence_optimization.py')


def test_calculation_only_patch_contract():
    text = PATCH.read_text()
    assert [line.split(' b/')[1] for line in text.splitlines()
            if line.startswith('diff --git ')] == ['bindcraft/' + name for name in FILES]
    # Exact five Python diffs from PR17 (90f5496), excluding every preset/example.
    assert hashlib.sha256(PATCH.read_bytes()).hexdigest() == '1ea606300faef97fbbd0c0161283ba78b9c8ff0492746f82f0cb691871928aca'
    assert text.count('+                sequence_features, sequence_profile = prepare_design_sequence_features(sequence, flags, softmax_weight, one_hot_weight, temperature, logit_scale, self.amino_acid_bias)') == 2
    assert '+    alphafold_model = AlphaFoldDesignModel(' in text
    assert '+        for amino_acid, bias' not in text


@pytest.fixture
def patched_source(tmp_path):
    source = os.environ.get('BMS_TEST_BC2_UPSTREAM')
    if not source:
        pytest.skip('set BMS_TEST_BC2_UPSTREAM to pristine d5bae16 for patch/native qualification')
    source = Path(source)
    stage = tmp_path / 'native'
    stage.mkdir()
    # Only these native Python files are needed for application and AST checks.
    names = (*FILES, 'MPNN_stage.py')
    (stage / 'bindcraft').mkdir()
    for name in names:
        shutil.copy2(source / 'bindcraft' / name, stage / 'bindcraft' / name)
    def apply(*args):
        result = subprocess.run(['git', 'apply', *args], cwd=stage, text=True,
                                capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    apply('--check', str(ROOT / 'apptainer/bindcraft2-producer.patch'))
    apply(str(ROOT / 'apptainer/bindcraft2-producer.patch'))
    before = {name: (stage / 'bindcraft' / name).read_bytes() for name in names}
    apply('--check', str(PATCH))
    apply(str(PATCH))
    after = {name: (stage / 'bindcraft' / name).read_bytes() for name in names}
    for name in names:
        compile(after[name], name, 'exec')
    apply('--reverse', '--check', str(PATCH))
    apply('--reverse', str(PATCH))
    assert before == {name: (stage / 'bindcraft' / name).read_bytes() for name in names}
    apply(str(PATCH))
    assert after == {name: (stage / 'bindcraft' / name).read_bytes() for name in names}
    yield source, stage


def test_native_campaign_design_not_validation_and_both_af2_paths(patched_source):
    _, stage = patched_source
    tree = ast.parse((stage / 'bindcraft/campaign.py').read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'AlphaFoldDesignModel']
    assert len(calls) == 2
    biased = [call for call in calls if any(k.arg == 'amino_acid_bias' for k in call.keywords)]
    assert len(biased) == 1
    keyword = next(k for k in biased[0].keywords if k.arg == 'amino_acid_bias')
    assert ast.unparse(keyword.value) == 'design_settings.binder.amino_acid_bias'
    af2 = ast.parse((stage / 'bindcraft/af2.py').read_text())
    calls = [n for n in ast.walk(af2) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == 'prepare_design_sequence_features']
    assert len(calls) == 2
    assert all(ast.unparse(call.args[-1]) == 'self.amino_acid_bias' for call in calls)


# Execute imported repaired-image functions, checking their installed source
# against the exact backport. Models/weights are never constructed.
NATIVE = r'''
import ast, sys
from pathlib import Path
from types import SimpleNamespace
import jax
import jax.numpy as jnp
import numpy as np
from bindcraft import sequence_optimization as so, af2, proteinmpnn as mp, protein_preparation as prep
from bindcraft.protein import AMINO_ACIDS, Protein, ResidueFlags
from bindcraft.settings import resolve_amino_acid_bias, resolve_omitted_amino_acids
assert jax.default_backend() == 'cpu'
source, stage = map(Path, sys.argv[1:])
def load(module, root, names, extra=None):
    namespace = dict(vars(module))
    namespace.update(extra or {})
    tree = ast.parse((root / 'bindcraft' / Path(module.__file__).name).read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(selected) == len(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(root), 'exec'), namespace)
    return namespace
for name in ('af2.py', 'campaign.py', 'protein_preparation.py', 'proteinmpnn.py', 'sequence_optimization.py'):
    assert Path(so.__file__).with_name(name).read_bytes() == (stage / 'bindcraft' / name).read_bytes(), name
new = so.sequence_features_from_logits
old = load(so, source, ['sequence_features_from_logits'])['sequence_features_from_logits']
prepare = af2.prepare_design_sequence_features
mpnew = mp.proteinmpnn_input_features
mpold = load(mp, source, ['proteinmpnn_input_features'])['proteinmpnn_input_features']
init = prep.prepare_binder_chains
settings = {'aa_bias': {'W': .3, 'Y': 2., 'C': 0, 'A': -1, 'F': 1.}}
biases = resolve_amino_acid_bias(settings)
assert resolve_omitted_amino_acids(settings) == ''.join(a for a in AMINO_ACIDS if a in 'CA')
bias = jnp.asarray([biases.get(a, 0) for a in AMINO_ACIDS])
w, y, f, c = map(AMINO_ACIDS.index, 'WYFC')
logits = jnp.linspace(-.01, .01, 40).reshape(2, 20)
onehot = jax.nn.one_hot(jnp.array([w, y]), 20)
flags = jnp.array([int(ResidueFlags.DESIGN), 0])
for temperature in [.01, .1, 1., 2.]:
    for scale in [.5, 1., 2.]:
        baseline = new(logits, 1., 0., temperature, scale)
        biased = new(logits, 1., 0., temperature, scale, bias)
        ratio = (biased[:, w] / biased[:, f]) / (baseline[:, w] / baseline[:, f])
        np.testing.assert_allclose(ratio, .3, rtol=2e-5)
        np.testing.assert_allclose((biased[:, y]/biased[:, f])/(baseline[:, y]/baseline[:, f]), 2., rtol=2e-5)
        np.testing.assert_array_equal(new(logits, 0., 0., temperature, scale, bias), logits)
        for soft, hard in [(0., 0.), (.4, 0.), (1., 0.), (1., 1.)]:
            np.testing.assert_array_equal(new(logits, soft, hard, temperature, scale), old(logits, soft, hard, temperature, scale))
        features, profile = prepare(onehot, flags, 1., 0., temperature, scale, bias)
        np.testing.assert_array_equal(features[1], onehot[1])
        np.testing.assert_array_equal(profile[1], onehot[1])
        omitted = logits.at[:, c].set(so.OMITTED_AMINO_ACID_LOGIT)
        assert np.all(np.asarray(new(omitted, 1., 0., temperature, scale, bias))[:, c] == 0)
        assert np.all(np.asarray(new(omitted, 0., 0., temperature, scale, bias))[:, c] == 0)
    args = (jnp.zeros((2, 37, 3)), jnp.ones(2), jnp.arange(2), jnp.ones(2),
            jnp.array([False, True]), onehot, temperature, jax.random.PRNGKey(0))
    n, o = mpnew(*args), mpold(*args)
    for name in n:
        np.testing.assert_array_equal(n[name], o[name])
    b = mpnew(*args, redesigned_amino_acid_bias=bias.at[c].set(so.OMITTED_AMINO_ACID_LOGIT))
    np.testing.assert_array_equal(b['bias'][1], o['bias'][1])
    probabilities = jax.nn.softmax(b['bias'][0] / temperature)
    wi, fi = mp._MPNN_ALPHABET.index('W'), mp._MPNN_ALPHABET.index('F')
    np.testing.assert_allclose(probabilities[wi] / probabilities[fi], .3, rtol=2e-5)
    assert probabilities[mp._MPNN_ALPHABET.index('C')] == 0
binder = SimpleNamespace(lengths=(3,), scaffold=None, amino_acid_bias=biases, omitted_amino_acids='C')
design = SimpleNamespace(binder=binder, binder_chains=('B',), settings={})
key = jax.random.PRNGKey(17)
initial = init(design, key)['B']
raw = Protein.empty(3, jax.random.split(key)[1])
np.testing.assert_array_equal(initial.sequence[:, w], raw.sequence[:, w])
assert np.all(np.asarray(initial.sequence[:, c]) == so.OMITTED_AMINO_ACID_LOGIT)
# Exercise the native scaffold branch with synthetic structure I/O only.
framework = raw.replace(sequence=jax.nn.one_hot(jnp.array([c, w, y]), 20),
                        flags=jnp.array([0, int(ResidueFlags.DESIGN), 0]))
scaffold_io = SimpleNamespace(empty=Protein.empty, from_structure=lambda *a, **k:
    {'A': SimpleNamespace(with_scaffold=lambda *a: framework)})
scaffold_init = load(prep, stage, ['prepare_binder_chains'],
    {'Protein': scaffold_io, 'structure_chain_names': lambda _: ['A']})['prepare_binder_chains']
binder.scaffold, binder.scaffold_edits = 'synthetic-only', 'fixture'
scaffold = scaffold_init(design, key)['B']
np.testing.assert_array_equal(scaffold.sequence[jnp.array([0, 2])], framework.sequence[jnp.array([0, 2])])
assert scaffold.sequence[1, c] == so.OMITTED_AMINO_ACID_LOGIT
assert scaffold.sequence[1, w] == framework.sequence[1, w]
# Differentiable softmax path and straight-through gradients remain finite.
grad = jax.grad(lambda x: new(x, 1., 1., .1, 2., bias)[:, w].sum())(logits)
assert np.isfinite(np.asarray(grad)).all() and np.any(np.asarray(grad) != 0)
print('BC2_AA_BIAS_NATIVE_CPU_OK')
'''


def test_native_jax_cpu_propensities_omissions_and_fixed_residues(patched_source):
    source, stage = patched_source
    python = os.environ.get('BMS_TEST_BC2_PYTHON')
    image = os.environ.get('BMS_TEST_BC2_IMAGE')
    if not python and not image:
        pytest.skip('set BMS_TEST_BC2_PYTHON or BMS_TEST_BC2_IMAGE for native JAX CPU checks')
    env = {**os.environ, 'JAX_PLATFORMS': 'cpu', 'JAX_PLATFORM_NAME': 'cpu'}
    if python:
        command = [python]
    else:
        command = ['apptainer', 'exec', '--no-home', '--bind', f'{source}:{source}:ro',
                   '--bind', f'{stage}:{stage}:ro', '--env', 'JAX_PLATFORMS=cpu,JAX_PLATFORM_NAME=cpu',
                   image, 'python3']
    result = subprocess.run([*command, '-c', NATIVE, str(source), str(stage)], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'BC2_AA_BIAS_NATIVE_CPU_OK' in result.stdout
