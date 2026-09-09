"""Actual streaming HTTP client and ASGI disconnect lifecycle, no live network."""
import asyncio

import httpx
import pytest

from test_bioxp_camera_boundary import Boundary
from pathlib import Path

JPEG = (Path(__file__).parent/'fixtures/bioxp_camera_producer.jpg').read_bytes()
PART = b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(JPEG)).encode() + b'\r\n\r\n' + JPEG + b'\r\n'


class ViewerStream(httpx.AsyncByteStream):
    def __init__(self, *, close_error=False):
        self.detached = asyncio.Event()
        self.more = asyncio.Event()
        self.close_count = 0
        self.close_error = close_error

    async def __aiter__(self):
        yield PART
        await self.more.wait()
        yield PART
        await asyncio.Event().wait()

    async def aclose(self):
        # A checkpoint is important: a cancelled Starlette scope must not skip
        # transport close or connection release at the first awaited operation.
        await asyncio.sleep(0)
        self.close_count += 1
        self.detached.set()
        if self.close_error:
            raise RuntimeError('synthetic upstream close failure')


def scope(generation, *, spec='2.3'):
    return {'type':'http', 'asgi':{'version':'3.0', 'spec_version':spec},
            'method':'GET', 'path':'/camera/mjpeg', 'raw_path':b'/camera/mjpeg',
            'query_string':f'expected_generation={generation}'.encode(),
            'headers':[], 'scheme':'http', 'server':('bms',80), 'client':('browser',1), 'http_version':'1.1'}


@pytest.mark.parametrize('detach', ['browser', 'proxy-start', 'proxy-body', 'upstream-close-error'])
def test_stream_detachment_releases_http_response_and_generation_lease(tmp_path, detach):
    async def scenario():
        b = Boundary(tmp_path)
        generation = await b.connect()
        stream = ViewerStream(close_error=detach == 'upstream-close-error')
        original = b.transport
        async def transport(request):
            if request.url.path == '/camera/mjpeg':
                return httpx.Response(200, headers={'Content-Type':'multipart/x-mixed-replace; boundary=frame'}, stream=stream)
            return await original(request)
        # Replace only the injected transport, never the client or lease/projection.
        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        sent = asyncio.Event()
        messages = []
        async def receive():
            await sent.wait()
            return {'type':'http.disconnect'}
        async def send(message):
            messages.append(message)
            if detach == 'proxy-start' and message['type'] == 'http.response.start':
                raise OSError('proxy detached before iterator started')
            if message['type'] == 'http.response.body' and message.get('body'):
                if detach == 'proxy-body':
                    raise OSError('proxy detached after frame')
                sent.set()
        try:
            await asyncio.wait_for(b.app(scope(generation, spec='2.4' if detach.startswith('proxy') else '2.3'), receive, send), 2)
        except Exception as exc:
            assert detach != 'browser', repr(exc)
        assert stream.detached.is_set()
        assert stream.close_count == 1
        assert b.connection._generation_leases[generation].lease_count == 0
        if detach == 'browser':
            assert next(m['body'] for m in messages if m['type'] == 'http.response.body') == PART
        await b.connection.disconnect()
        assert b.clients[0]._client.is_closed
    asyncio.run(scenario())


def test_disconnect_reconnect_rejects_late_stream_open(tmp_path):
    async def scenario():
        b = Boundary(tmp_path)
        old = await b.connect()
        entered, release = asyncio.Event(), asyncio.Event()
        stream = ViewerStream()
        async def transport(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, headers={'Content-Type':'multipart/x-mixed-replace; boundary=frame'}, stream=stream)
        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        messages = []
        async def receive():
            await asyncio.Event().wait()
        async def send(message):
            messages.append(message)
        pending = asyncio.create_task(b.app(scope(old), receive, send))
        await entered.wait()
        lease = b.connection._generation_leases[old]
        disconnecting = asyncio.create_task(b.connection.disconnect())
        while b.connection.snapshot().generation == old:
            await asyncio.sleep(0)
        new = (await b.connection.connect()).generation
        release.set()
        await asyncio.wait_for(pending, 2)
        await disconnecting
        assert messages[0]['status'] == 409
        assert stream.close_count == 1
        assert lease.lease_count == 0
        assert new != old
        await b.connection.disconnect()
    asyncio.run(scenario())
