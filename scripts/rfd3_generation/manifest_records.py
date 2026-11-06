"""Incremental existing-manifest JSON reader for the shared artifact receiver.

Events are (top_level_key, value), except each candidates array member is a
('candidate', record) event. The caller keeps its existing path/hash validation
and durable Job state owner. No population-sized read() or list is required.
"""
from __future__ import annotations

import json
from pathlib import Path


def iter_manifest_records(path: Path, *, chunk_size=65536, record_limit=8 * 1024 * 1024):
    decoder = json.JSONDecoder()
    with path.open('r', encoding='utf-8') as handle:
        buffer = ''
        eof = False

        def more():
            nonlocal buffer, eof
            chunk = handle.read(chunk_size)
            eof = not chunk
            buffer += chunk

        def whitespace():
            nonlocal buffer
            buffer = buffer.lstrip()
            while not buffer and not eof:
                more()
                buffer = buffer.lstrip()

        def character(expected):
            nonlocal buffer
            whitespace()
            if not buffer.startswith(expected):
                raise ValueError(f'malformed manifest: expected {expected!r}')
            buffer = buffer[len(expected):]

        def value():
            nonlocal buffer
            whitespace()
            while True:
                try:
                    result, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError as exc:
                    if eof or len(buffer) > record_limit:
                        raise ValueError('truncated, malformed or oversized manifest record') from exc
                    more()
                    continue
                # Fetch the delimiter before accepting a scalar that could end
                # exactly at a chunk boundary (e.g. 1 followed by 00).
                if end == len(buffer) and not eof:
                    more()
                    continue
                if end > record_limit:
                    raise ValueError('oversized manifest record')
                buffer = buffer[end:]
                return result

        character('{')
        seen = set()
        first = True
        while True:
            whitespace()
            if buffer.startswith('}'):
                character('}')
                break
            if not first:
                character(',')
            key = value()
            if not isinstance(key, str) or key in seen:
                raise ValueError('manifest keys must be unique strings')
            seen.add(key)
            character(':')
            if key == 'candidates':
                character('[')
                whitespace()
                if not buffer.startswith(']'):
                    while True:
                        candidate = value()
                        if not isinstance(candidate, dict):
                            raise ValueError('candidate record must be an object')
                        yield 'candidate', candidate
                        whitespace()
                        if buffer.startswith(']'):
                            break
                        character(',')
                character(']')
            else:
                yield key, value()
            first = False
        whitespace()
        if buffer or not eof:
            raise ValueError('trailing manifest data')
        if 'candidates' not in seen:
            raise ValueError('manifest has no candidates array')
