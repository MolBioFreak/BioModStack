"""Anonymous pinned-source transport. Redirect locations are ephemeral, never receipts.

Policies are trusted release metadata, not request inputs. Exact DNS hostnames
are reviewed; every resolved address must be global and connections pin that
resolution (including TLS hostname verification). No proxies, cookies or auth.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse


class TransportError(RuntimeError):
    pass


def validate_policy(policy, source_url):
    if policy is None:
        return
    if (not isinstance(policy, dict) or set(policy) != {
            'source_url', 'approval_ref', 'allowed_authorities', 'max_hops'}
            or policy['source_url'] != source_url
            or not isinstance(policy['approval_ref'], str)
            or not policy['approval_ref'].strip()
            or type(policy['max_hops']) is not int
            or not 1 <= policy['max_hops'] <= 5
            or not isinstance(policy['allowed_authorities'], (list, tuple))
            or not policy['allowed_authorities']):
        raise TransportError('invalid reviewed redirect policy')
    for authority in policy['allowed_authorities']:
        if not isinstance(authority, str):
            raise TransportError('invalid redirect authority')
        parsed = urllib.parse.urlsplit('https://' + authority)
        if (parsed.netloc != authority or parsed.path or parsed.query or parsed.fragment
                or parsed.username or parsed.password or not parsed.hostname
                or '*' in authority or authority != authority.lower()
                or parsed.hostname.endswith('.')):
            raise TransportError('invalid redirect authority')
        try:
            parsed.port
        except ValueError:
            raise TransportError('invalid redirect port') from None


def _destination(url, *, test_only):
    try:
        parsed = urllib.parse.urlsplit(url)
        fixture = test_only and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
        if (not parsed.hostname or parsed.username or parsed.password or parsed.fragment
                or (parsed.scheme != 'https' and not fixture)
                or (not fixture and parsed.port not in (None, 443))
                or any(ord(c) < 33 or ord(c) == 127 for c in url)
                or '\\' in url):
            raise ValueError
        port = parsed.port or 443
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global
                                for a in addresses) and not fixture:
            raise ValueError
        return parsed, addresses[0][4][0], port, fixture
    except (ValueError, OSError):
        raise TransportError('unsafe or unresolved transport destination') from None


class _Response:
    """Keep HTTP/1.1 connection alive until the final body has been consumed."""
    def __init__(self, response, connection):
        self.response = response
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        try:
            self.response.close()
        finally:
            self.connection.close()


def open_response(source_url, *, policy, offset, timeout, deadline, test_only=False):
    """Return final HTTPResponse; caller closes it. Never forward server headers."""
    validate_policy(policy, source_url)
    url = source_url
    hops = 0
    while True:
        parsed, address, port, fixture = _destination(url, test_only=test_only)
        remaining = min(timeout, deadline - time.monotonic())
        if remaining <= 0:
            raise TransportError('transport deadline exceeded')
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=remaining)
        try:
            connection.sock = socket.create_connection((address, port), timeout=remaining)
            if not fixture:
                connection.sock = ssl.create_default_context().wrap_socket(
                    connection.sock, server_hostname=parsed.hostname)
            path = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
            connection.request('GET', path, headers={'Accept-Encoding': 'identity',
                               **({'Range': f'bytes={offset}-'} if offset else {})})
            response = connection.getresponse()
            if response.status not in (301, 302, 303, 307, 308):
                return _Response(response, connection)
            location = response.getheader('Location')
            response.close()
            connection.close()
            if policy is None:
                raise TransportError('redirect requires reviewed transport policy')
            if hops >= policy['max_hops'] or not location:
                raise TransportError('redirect hop limit or missing location')
            destination = urllib.parse.urljoin(url, location)
            authority = urllib.parse.urlsplit(destination).netloc
            if authority not in policy['allowed_authorities']:
                raise TransportError('redirect authority not approved')
            # Validate before any connection; source remains immutable authority.
            url = destination
            hops += 1
        except (OSError, ValueError, http.client.HTTPException):
            connection.close()
            raise OSError('anonymous transport failed') from None
        except BaseException:
            connection.close()
            raise
