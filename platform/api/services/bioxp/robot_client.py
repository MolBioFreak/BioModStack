from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from .errors import RobotResponseError, RobotTransportError
from .target_policy import ValidatedBioXpTarget


DEFAULT_ROBOT_ROUTES: Mapping[str, tuple[str, str, float]] = {
    "status": ("GET", "/status", 5.0),
    "collect_hardware_snapshot": ("POST", "/hardware/snapshot/collect", 180.0),
    "construct_pipettes": ("POST", "/oem/startup/constructor_pipettes", 280.0),
    "initialize_without_motion": ("POST", "/oem/startup/initialize_without_motion", 140.0),
    "run_initial_check": ("POST", "/oem/initial_check", 75.0),
    "emergency_stop": ("POST", "/oem/runtime/emergency_stop", 5.0),
}


class PinnedAddressTransport(httpx.AsyncBaseTransport):
    """Connect to one policy-validated address while preserving HTTP/TLS identity."""

    def __init__(
        self,
        target: ValidatedBioXpTarget,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not target.resolved_addresses:
            raise ValueError("BioXP transport requires a policy-validated address")
        self._target = target
        self._address = str(target.resolved_addresses[0])
        self._transport = transport or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != self._target.hostname:
            raise httpx.UnsupportedProtocol("BioXP transport target changed after validation")
        extensions = dict(request.extensions)
        if self._target.scheme == "https":
            extensions["sni_hostname"] = self._target.hostname
        pinned_request = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=self._address),
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._transport.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        await self._transport.aclose()


class BioXpRobotClient:
    """One generation-bound transport; callers use registry keys, never paths."""

    def __init__(
        self,
        target: ValidatedBioXpTarget,
        *,
        routes: Mapping[str, tuple[str, str, float]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.target = target
        self.routes = dict(routes or DEFAULT_ROBOT_ROUTES)
        pinned_transport = PinnedAddressTransport(target, transport=transport)
        self._client = httpx.AsyncClient(
            base_url=target.api_url,
            transport=pinned_transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0),
        )

    async def probe(self) -> dict[str, Any]:
        payload = await self.request("status", retry_read_once=True)
        if not isinstance(payload, dict):
            raise RobotTransportError("BioXP status response was not an object")
        return payload

    async def request(
        self,
        route_name: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_read_once: bool = False,
    ) -> Any:
        try:
            method, path, timeout = self.routes[route_name]
        except KeyError as exc:
            raise RobotTransportError(f"Unknown BioXP robot route key: {route_name}") from exc
        attempts = 2 if retry_read_once and method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json_data,
                    params=params,
                    timeout=timeout,
                )
                if 300 <= response.status_code < 400:
                    raise RobotTransportError("BioXP target redirects are forbidden")
                if response.is_error:
                    try:
                        detail: object = response.json()
                    except ValueError:
                        detail = response.text[:1000]
                    raise RobotResponseError(response.status_code, detail)
                return response.json()
            except RobotResponseError:
                raise
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                if attempt + 1 < attempts:
                    continue
                raise RobotTransportError("BioXP robot transport is unreachable or timed out") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise RobotTransportError("BioXP robot returned an invalid transport response") from exc
        raise RobotTransportError("BioXP robot transport failed")

    async def close(self) -> None:
        await self._client.aclose()
