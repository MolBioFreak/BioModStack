"""Catalog presentation serialization; scientific model ownership is unchanged."""
from copy import deepcopy

PRESENTATION_FIELDS = ('accepted_types', 'label', 'units', 'group', 'ui_control',
                       'step', 'applicability', 'required_when', 'nullable_when')


def serialize_parameter(parameter) -> dict:
    """One projection for list/detail consumers, including nested native schemas."""
    result = parameter.model_dump()
    for name in PRESENTATION_FIELDS:
        value = getattr(parameter, name, None)
        if value is not None:
            result[name] = deepcopy(value)
    return result
