"""Project native process-profile resources into the existing execution plan.

Read literal named selectors from the selected committed Nextflow profiles. This
is metadata projection, not another resource policy or admission condition.
"""
from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path

from component_runtime import canonical_bytes
import json


def _blocks(text: str):
    """Yield brace blocks while ignoring braces in comments and quoted strings."""
    stack = []
    line_start = 0
    quote = None
    escaped = False
    comment = None
    i = 0
    while i < len(text):
        char = text[i]
        pair = text[i:i + 2]
        if comment == 'line':
            if char == '\n':
                comment = None
                line_start = i + 1
        elif comment == 'block':
            if pair == '*/':
                comment = None
                i += 1
        elif quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif pair in {'//', '/*'}:
            comment = 'line' if pair == '//' else 'block'
            i += 1
        elif char in {'"', "'"}:
            quote = char
        elif char == '\n':
            line_start = i + 1
        elif char == '{':
            stack.append((text[line_start:i].strip(), i + 1))
        elif char == '}' and stack:
            header, start = stack.pop()
            yield header, start, i, text[start:i]
        i += 1


def named_profile_overrides(config: str, profiles: tuple[str, ...], native_name: str):
    blocks = list(_blocks(config))
    root = next((row for row in blocks if row[0] == 'profiles'), None)
    if root is None:
        return {}
    result = {}
    # The legacy Nextflow parser merges activated profiles in declaration order.
    active = sorted((row for row in blocks if row[0] in profiles
                     and root[1] <= row[1] < row[2] <= root[2]), key=lambda row: row[1])
    for profile, _, _, body in active:
        for header, _, _, selector_body in _blocks(body):
            if not header.startswith('withName:'):
                continue
            selector = header.partition(':')[2].strip()
            try:
                selector = ast.literal_eval(selector)
            except (ValueError, SyntaxError):
                pass  # Bare native process names are valid Nextflow selectors.
            try:
                selected = isinstance(selector, str) and re.fullmatch(selector, native_name)
            except re.error:
                selected = False
            if not selected:
                continue
            for match in re.finditer(r'^\s*(cpus|memory|time|maxForks)\s*=\s*(.*?)\s*(?://[^\n]*)?$',
                                     selector_body, re.MULTILINE):
                field, expression = match.groups()
                try:
                    value = ast.literal_eval(expression)
                except (ValueError, SyntaxError):
                    continue  # Keep the existing projection for dynamic closures.
                if type(value) not in {int, float, str}:
                    continue
                result[field] = (value, f'nextflow.config:profiles.{profile}.process.withName.{selector}.{field}')
    return result


def bind_selected_profile_resources(metadata, *, profiles: tuple[str, ...], config_path: Path):
    if not profiles:
        return metadata
    config = config_path.read_text()

    def bind(component):
        native_name = component.authority.rsplit(':', 1)[-1]
        overrides = named_profile_overrides(config, profiles, native_name)
        if not overrides:
            return component
        resources = json.loads(component.resources_json)
        for field, (value, authority) in overrides.items():
            if field in {'cpus', 'memory'}:
                resources[field] = {'value': value, 'authority': authority}
            else:
                resources['max_forks' if field == 'maxForks' else field] = value
        return replace(component, resources_json=canonical_bytes(resources))

    return replace(metadata,
        static_components=tuple(bind(row) for row in metadata.static_components),
        dynamic_templates=tuple(bind(row) for row in metadata.dynamic_templates))
