"""Consume byte-for-byte isolated FFmpeg/provider exports via real BMS routes."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from test_bioxp_camera_boundary import Boundary
from test_bioxp_camera_stream_boundary import scope


@pytest.mark.parametrize('stem', ['ffmpeg-2-testsrc2', 'ffmpeg-30-testsrc2', 'ffmpeg-30-color'])
def test_real_ffmpeg_export_through_proxy_and_stop(tmp_path, stem):
    export = os.environ.get('CAMERA_TEST_EXPORT')
    if not export:
        pytest.skip('requires isolated robot camera export; see lane runner')
    output = Path(export)
    row = json.loads((output / (stem + '.json')).read_text())
    encoded = (output / (stem + '.mjpeg')).read_bytes()
    stopped = json.loads((output / (stem + '-stopped.json')).read_text())

    async def scenario():
        boundary = Boundary(tmp_path)
        original = boundary.transport
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for offset in range(0, len(encoded), 997):
                    yield encoded[offset:offset+997]
        async def transport(request):
            if request.url.path == '/camera/mjpeg':
                return httpx.Response(200, headers={'Content-Type': 'multipart/x-mixed-replace; boundary=frame'}, stream=Stream())
            return await original(request)
        boundary.transport = transport
        generation = await boundary.connect()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app), base_url='http://bms') as browser:
            boundary.payload = row['status']
            status = await browser.get('/camera/status', params={'expected_generation': generation})
            assert status.status_code == 200, status.text
            boundary.payload = row['stream']
            stream = await browser.get('/camera/stream/state', params={'expected_generation': generation})
            assert stream.status_code == 200, stream.text
            messages = []
            async def receive():
                await asyncio.Event().wait()
            async def send(message):
                messages.append(message)
            reader_scope = scope(generation, spec='2.4')
            reader_scope['query_string'] += ('&stream_id=' + row['stream']['stream_id']).encode()
            await boundary.app(reader_scope, receive, send)
            body = b''.join(m.get('body', b'') for m in messages)
            assert messages[0]['status'] == 200
            assert body == encoded
            assert boundary.connection._generation_leases[generation].lease_count == 0
            (output / (stem + '-proxy.mjpeg')).write_bytes(body)
            samples = []
            for sample in row['samples']:
                boundary.payload = sample
                response = await browser.get('/camera/status', params={'expected_generation': generation})
                assert response.status_code == 200, response.text
                samples.append(response.json())
            (output / (stem + '-ui.json')).write_text(json.dumps({'status': status.json(), 'samples': samples, 'stream': stream.json()}))
            boundary.payload = stopped
            response = await browser.post('/camera/stream/stop', json={'expected_generation': generation})
            assert response.status_code == 200, response.text
            assert response.json()['active'] is False
            assert response.json()['state'] == 'off'
            print({'stem': stem, 'proxied_bytes': len(body), 'frames': row['status']['frame_sequence'], 'stop_status': response.status_code})
        await boundary.connection.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize('query', ['stream_id=', 'stream_id=' + 'x'*129, 'stream_id=bad%20id', 'fps=1'])
def test_mjpeg_reader_identity_remains_bounded(tmp_path, query):
    async def scenario():
        boundary = Boundary(tmp_path)
        generation = await boundary.connect()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app), base_url='http://bms') as browser:
            response = await browser.get(f'/camera/mjpeg?expected_generation={generation}&{query}')
            assert response.status_code == 422
            assert boundary.paths == ['/status']
        await boundary.connection.disconnect()
    asyncio.run(scenario())
