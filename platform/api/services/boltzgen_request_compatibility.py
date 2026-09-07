"""Known BoltzGen CSV/NPZ compatibility, before queue admission.

scripts/lib/filtering/evidence.py owns the native dialect mapping. Native pLDDT
is not supplied on this path; design_ptm is a distinct fraction, never its alias.
"""
import math
import re

from pydantic import BaseModel, ConfigDict, Field


class BoltzGenRankWeights(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    design_ptm: float | None = Field(default=1., gt=0, allow_inf_nan=False)
    affinity_probability: float | None = Field(default=1., gt=0, allow_inf_nan=False)
    filter_rmsd: float | None = Field(default=1., gt=0, allow_inf_nan=False)


def compile_boltzgen_settings(params):
    result = dict(params)
    for key in ('min_plddt', 'boltzgen_min_plddt'):
        if result.get(key) is not None:
            raise ValueError('BoltzGen native pLDDT is unavailable on the known CSV/NPZ producer path; disable this threshold (null)')
    if re.search(r'\bplddt\s*[<>]', str(result.get('boltzgen_additional_filters') or '')):
        raise ValueError('BoltzGen native pLDDT is unavailable; additional pLDDT filters are incompatible')
    values = {}
    legacy = result.get('boltzgen_metrics_override')
    if legacy:
        if not isinstance(legacy, str):
            raise ValueError('metric override must be a string')
        for token in re.split(r'[\s,]+', legacy.strip()):
            try:
                name, raw = token.split('=')
                name = {'conf_score': 'affinity_probability', 'rmsd': 'filter_rmsd'}.get(name, name)
                if name not in BoltzGenRankWeights.model_fields or name in values:
                    raise ValueError('unknown or duplicate metric')
                values[name] = None if raw.lower() == 'none' else float(raw)
            except ValueError as exc:
                raise ValueError('unsupported BoltzGen rank override') from exc
    for name in BoltzGenRankWeights.model_fields:
        key = f'boltzgen_rank_{name}_weight'
        if key in result:
            value = result[key]
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                raise ValueError('rank weights require finite positive numbers or null (disabled)')
            if name in values and value != values[name]:
                raise ValueError('conflicting typed and legacy rank weights')
            values[name] = value
    weights = BoltzGenRankWeights.model_validate(values).model_dump()
    result.update({f'boltzgen_rank_{name}_weight': weight for name, weight in weights.items()})
    result['boltzgen_metrics_override'] = ' '.join(f'{name}={"none" if weight is None else weight}' for name, weight in weights.items())
    result['boltzgen_effective_rank'] = [
        {'name': name, 'higher_is_better': name != 'filter_rmsd', 'weight': weight,
         'unit': 'angstrom' if name == 'filter_rmsd' else 'fraction'}
        for name, weight in weights.items() if weight is not None]
    return result
