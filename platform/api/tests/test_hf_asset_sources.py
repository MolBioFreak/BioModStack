"""Offline controller broker qualification; HF HTTP/SDK boundaries are doubles."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace

import pytest

from services.remote_execution import hf_assets as broker
from services.remote_execution import hf_assets_worker as worker


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in broker._KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    token = tmp_path / 'token'
    token.write_text('hf_testcredential123456')
    token.chmod(0o600)
    monkeypatch.setenv('BMS_HF_ASSET_BUCKET', 'owner/private-assets')
    monkeypatch.setenv('BMS_HF_TOKEN_FILE', str(token))
    return token


@pytest.fixture
def capability():
    expires = int(time.time()) + 600
    return {'url': f'https://us.aws.cdn.hf.co/object?Signature=abc&Policy=abc&Key-Pair-Id=test&Expires={expires}',
            'expires_at': expires}


@pytest.fixture
def artifact(tmp_path):
    path = tmp_path / 'artifact'
    data = b'x' * broker.MIN_BYTES
    path.write_bytes(data)
    return SimpleNamespace(source=path, role='image', sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


def request(entry):
    return dict(role=entry.role, sha256=entry.sha256, size_bytes=entry.size_bytes, path=str(entry.source))


class Api:
    def __init__(self, entry, present=True):
        self.entry = entry
        self.present = present
        self.uploads = []
        self.info = SimpleNamespace(id='owner/private-assets', private=True, size=0)
        self.corrupt_size = False
        self.fail_upload = False
        self.mutate = False
        self.probes = 0

    def bucket_info(self, bucket):
        return self.info

    def get_bucket_paths_info(self, bucket, paths):
        self.probes += 1
        return [SimpleNamespace(path=paths[0], size=self.entry.size_bytes + int(self.corrupt_size))] if self.present else []

    def batch_bucket_files(self, bucket, *, add):
        self.uploads.append(add)
        # Exercise actual retained-inode SDK path, not a copied substitute.
        path, key = add[0]
        assert path.startswith('/proc/self/fd/')
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == self.entry.sha256
        self.present = True
        if self.mutate:
            self.entry.source.write_bytes(b'z' * self.entry.size_bytes)
        if self.fail_upload:
            raise RuntimeError('secret-url token PRIVATE')


class Http:
    def __init__(self, source):
        self.source = source
        self.calls = []
        self.status = 302

    def head(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert url.startswith('https://huggingface.co/buckets/owner/private-assets/resolve/')
        assert kwargs['follow_redirects'] is False
        assert '%2F' in url  # exact pinned SDK resolve-path encoding
        return SimpleNamespace(status_code=self.status, headers={'location': self.source['url']})


def test_unconfigured_readiness_no_network():
    assert broker.configuration() is None
    assert broker.readiness() == dict(configured=False, available=False, bucket=None, mode='ssh',
                                    credential_configured=False, allow_publish=False,
                                    message='HF is not configured; SSH delivery is available')


def test_configuration_safe_repr_and_endpoint(configured, monkeypatch):
    monkeypatch.setenv('HF_ENDPOINT', 'https://attacker.invalid')
    config = broker.configuration()
    assert str(configured) not in repr(config)
    assert broker.ENDPOINT == 'https://huggingface.co'
    status = broker.readiness()
    assert not status['available'] and status['credential_configured']
    assert 'hf_test' not in json.dumps(status) and str(configured) not in json.dumps(status)


@pytest.mark.parametrize('mode', [0o644, 0o660, 0o777, 0o000])
def test_unsafe_credential_modes(configured, mode):
    configured.chmod(mode)
    with pytest.raises(broker.HFAssetError, match='missing or unsafe'):
        broker.configuration()
    assert not broker.readiness()['available']


@pytest.mark.parametrize('kind', ['symlink', 'directory', 'hardlink', 'oversize', 'empty', 'nonascii', 'relative', 'parent_symlink'])
def test_unsafe_credential_paths(configured, monkeypatch, kind):
    if kind == 'symlink':
        p = configured.with_name('link'); p.symlink_to(configured)
    elif kind == 'directory':
        p = configured.parent
    elif kind == 'hardlink':
        p = configured.with_name('link'); os.link(configured, p)
    elif kind == 'relative':
        p = Path('token')
    elif kind == 'parent_symlink':
        p = configured.parent.with_name(configured.parent.name + '-link'); p.symlink_to(configured.parent)
        p = p / configured.name
    else:
        p = configured
        p.write_bytes({'oversize': b'x' * 4097, 'empty': b'', 'nonascii': b'\xff'}[kind])
    monkeypatch.setenv('BMS_HF_TOKEN_FILE', str(p))
    with pytest.raises(broker.HFAssetError): broker.configuration()


@pytest.mark.parametrize('key,value', [('BMS_HF_ASSET_BUCKET','../oops'), ('BMS_HF_ASSET_BUCKET','https://secret'),
    ('BMS_HF_ASSET_ALLOW_PUBLISH','true'), ('BMS_HF_ASSET_MAX_BYTES','-1'), ('BMS_HF_ASSET_MAX_BYTES','0')])
def test_bad_settings(configured, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    assert broker.readiness()['bucket'] is None
    with pytest.raises(broker.HFAssetError): broker.configuration()


def test_partial_configuration_fails(monkeypatch):
    monkeypatch.setenv('BMS_HF_ASSET_ALLOW_PUBLISH', '0')
    with pytest.raises(broker.HFAssetError): broker.configuration()


def test_existing_cloud_hit_needs_no_local_file(configured, artifact, capability):
    api, http = Api(artifact), Http(capability)
    artifact.source.unlink()
    assert worker._prepare(request(artifact), broker.configuration(), api, http, 'private-token') == capability
    assert not api.uploads
    assert http.calls[0][1]['headers'] == {'Authorization': 'Bearer private-token'}


def test_publish_pinned_inode_and_no_overwrite(configured, monkeypatch, artifact, capability):
    monkeypatch.setenv('BMS_HF_ASSET_ALLOW_PUBLISH','1')
    api, http = Api(artifact, False), Http(capability)
    for _ in range(2):
        assert worker._prepare(request(artifact), broker.configuration(), api, http, 'token') == capability
    assert len(api.uploads) == 1
    assert api.uploads[0][0][1] == f'sha256/{artifact.sha256}/image.sif'


@pytest.mark.parametrize('failure', ['missing', 'public', 'owner', 'size', 'quota', 'hash', 'symlink', 'changed', 'uncertain'])
def test_fail_closed_no_capability(configured, monkeypatch, artifact, capability, failure):
    api, http = Api(artifact), Http(capability)
    if failure in {'missing','hash','symlink','changed','uncertain','quota'}:
        api.present = False
    if failure != 'missing': monkeypatch.setenv('BMS_HF_ASSET_ALLOW_PUBLISH','1')
    if failure == 'public': api.info.private = False
    if failure == 'owner': api.info.id = 'other/private-assets'
    if failure == 'size': api.corrupt_size = True
    if failure == 'quota': monkeypatch.setenv('BMS_HF_ASSET_MAX_BYTES',str(artifact.size_bytes - 1))
    if failure == 'hash': artifact.source.write_bytes(b'y' * artifact.size_bytes)
    if failure == 'symlink':
        target = artifact.source.with_name('link'); target.symlink_to(artifact.source); artifact.source = target
    if failure == 'changed': api.mutate = True
    if failure == 'uncertain': api.fail_upload = True
    with pytest.raises(broker.HFAssetError) as error:
        worker._prepare(request(artifact), broker.configuration(), api, http, 'PRIVATE')
    assert 'PRIVATE' not in str(error.value)
    assert not http.calls
    if failure == 'uncertain':
        assert api.probes >= 3
        api.fail_upload = False
        assert worker._prepare(request(artifact), broker.configuration(), api, http, 'PRIVATE') == capability
        assert len(api.uploads) == 1


@pytest.mark.parametrize('status', [200, 301, 401, 403, 429, 500])
def test_origin_status_fail_closed(configured, artifact, capability, status):
    http = Http(capability); http.status = status
    with pytest.raises(broker.HFAssetError):
        worker._prepare(request(artifact), broker.configuration(), Api(artifact), http, 'token')


@pytest.mark.parametrize('change', ['host', 'expired', 'duplicate', 'userinfo', 'fragment', 'scheme'])
def test_bad_source_sanitized(configured, artifact, capability, change):
    source = dict(capability)
    if change == 'host': source['url'] = source['url'].replace('us.aws.cdn.hf.co', 'evil.invalid')
    if change == 'expired': source['url'] = source['url'].replace(str(source['expires_at']), '1')
    if change == 'duplicate': source['url'] += '&Expires=123'
    if change == 'userinfo': source['url'] = source['url'].replace('https://', 'https://private@')
    if change == 'fragment': source['url'] += '#private'
    if change == 'scheme': source['url'] = source['url'].replace('https:', 'http:')
    with pytest.raises(broker.HFAssetError) as error:
        worker._prepare(request(artifact), broker.configuration(), Api(artifact), Http(source), 'token')
    assert 'https:' not in str(error.value)


@pytest.mark.asyncio
async def test_prepare_exact_contract_and_fresh_retry(configured, monkeypatch, artifact, capability):
    calls = []
    async def invoke(req, *, check_fence):
        calls.append(req); await check_fence(); return capability
    monkeypatch.setattr(broker, '_invoke', invoke)
    for _ in range(2):
        assert await broker.prepare_sources([artifact], check_fence=broker._noop) == {(True, artifact.sha256): capability}
    assert len(calls) == 2 and 'token' not in json.dumps(calls)


@pytest.mark.asyncio
async def test_prepare_small_skips_and_invalid_role_fails(configured, artifact, monkeypatch):
    async def forbidden(*a, **k): raise AssertionError('must not invoke')
    monkeypatch.setattr(broker, '_invoke', forbidden)
    artifact.size_bytes = 1
    assert await broker.prepare_sources([artifact], check_fence=broker._noop) == {}
    artifact.role = 'input'
    with pytest.raises(broker.HFAssetError): await broker.prepare_sources([artifact], check_fence=broker._noop)


@pytest.mark.asyncio
async def test_check_readonly_safe_failure(configured, monkeypatch):
    async def invoke(req, **kw):
        assert req == {'action':'check'}
        return {'available': True}
    monkeypatch.setattr(broker, '_invoke', invoke)
    assert (await broker.check_connection())['available']
    async def bad(*a, **k): raise broker.HFAssetError('safe failure')
    monkeypatch.setattr(broker, '_invoke', bad)
    assert not (await broker.check_connection())['available']


@pytest.mark.asyncio
@pytest.mark.parametrize('cause', ['cancel', 'fence', 'timeout'])
async def test_real_owned_subprocess_is_killed_and_reaped(monkeypatch, cause):
    original = asyncio.create_subprocess_exec
    children = []
    async def spawn(*args, **kwargs):
        # Real process lifecycle; replace only SDK executable with an offline sleeper.
        child = await original(sys.executable, '-c', 'import time; time.sleep(90)', **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def fence():
        if children and cause == 'fence': raise broker.HFAssetError('lease superseded')
    task = asyncio.create_task(broker._invoke({'action':'check'}, check_fence=fence, timeout=0.1 if cause == 'timeout' else 30))
    while not children: await asyncio.sleep(0.01)
    if cause == 'cancel': task.cancel()
    with pytest.raises(asyncio.CancelledError if cause == 'cancel' else broker.HFAssetError): await task
    assert children[0].returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError): os.kill(children[0].pid, 0)


@pytest.mark.asyncio
async def test_real_child_failure_does_not_expose_config(monkeypatch):
    monkeypatch.setenv('BMS_HF_ASSET_BUCKET', 'PRIVATE-INVALID')
    with pytest.raises(broker.HFAssetError) as error:
        await broker._invoke({'action':'check'}, check_fence=broker._noop)
    assert str(error.value) == broker.ERROR_MESSAGES['configuration']


@pytest.mark.asyncio
@pytest.mark.parametrize('code', list(broker.ERROR_MESSAGES) + ['https://private.invalid/?token=PRIVATE'])
async def test_nonzero_child_only_exposes_allowlisted_errors(monkeypatch, code):
    original = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        program = 'import json; print(json.dumps(' + repr({'error': code}) + ')); raise SystemExit(1)'
        return await original(sys.executable, '-c', program, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(broker.HFAssetError) as error:
        await broker._invoke({}, check_fence=broker._noop)
    assert str(error.value) == broker.ERROR_MESSAGES.get(code, broker.ERROR_MESSAGES['failed'])
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('status,expected', [(401,'authorization'), (403,'authorization'), (402,'quota'),
    (413,'quota'), (507,'quota'), (429,'throttled'), (503,'unavailable'), (400,'failed'), (None,'failed')])
def test_sdk_error_code_never_reflects_provider_prose(status, expected):
    error = RuntimeError('https://private.invalid/?token=PRIVATE')
    error.response = SimpleNamespace(status_code=status)
    assert worker._error_code(error) == expected
    assert worker._error_code(broker.HFAssetError('https://private.invalid/?token=PRIVATE')) == 'failed'


@pytest.mark.asyncio
async def test_cancel_during_real_spawn_reaps(monkeypatch):
    original = asyncio.create_subprocess_exec
    children = []
    release = asyncio.Event()
    async def spawn(*args, **kwargs):
        child = await original(sys.executable, '-c', 'import time; time.sleep(90)', **kwargs)
        children.append(child)
        await release.wait()
        return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    task = asyncio.create_task(broker._invoke({}, check_fence=broker._noop))
    while not children: await asyncio.sleep(0.01)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert children[0].returncode == -signal.SIGKILL


def test_actual_pinned_sdk_http_contract(configured, artifact, capability, monkeypatch):
    import httpx
    import huggingface_hub
    assert huggingface_hub.__version__ == '1.24.0'
    real_client = httpx.Client
    calls = []
    key = f'sha256/{artifact.sha256}/image.sif'
    def respond(req):
        calls.append((req.method, req.url.host, req.url.path))
        assert req.url.host == 'huggingface.co'
        assert req.headers['authorization'] == 'Bearer hf_testcredential123456'
        if req.method == 'GET' and req.url.path == '/api/buckets/owner/private-assets':
            return httpx.Response(200, json=dict(id='owner/private-assets', private=True,
                                  createdAt='2026-01-01T00:00:00Z', size=0, totalFiles=1))
        if req.method == 'POST' and req.url.path.endswith('/paths-info'):
            assert json.loads(req.content) == {'paths': [key]}
            return httpx.Response(200, json=[dict(type='file', path=key, size=artifact.size_bytes,
                                                  xetHash='a' * 64)])
        if req.method == 'HEAD' and '/resolve/' in req.url.path:
            return httpx.Response(302, headers={'location': capability['url']})
        raise AssertionError('unexpected HTTP request')
    class client(real_client):
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, 'Client', client)
    # _execute sanitizes SDK environment inside its owned process in production.
    # Preserve the test process environment explicitly around this direct call.
    saved = os.environ.copy()
    try:
        assert worker._execute(dict(action='prepare', **request(artifact))) == capability
        assert worker._execute({'action':'check'}) == {'available': True}
    finally:
        os.environ.clear(); os.environ.update(saved)
        huggingface_hub.set_client_factory(real_client)
    assert [item[0] for item in calls] == ['GET', 'POST', 'HEAD', 'GET']


@pytest.mark.asyncio
async def test_repeated_cancel_cannot_interrupt_reap_or_private_state_cleanup(monkeypatch):
    original = asyncio.create_subprocess_exec
    children, roots = [], []
    reaping, release = asyncio.Event(), asyncio.Event()
    async def spawn(*args, **kwargs):
        root = Path(kwargs['env']['BMS_HF_SDK_STATE'])
        roots.append(root)
        assert root.stat().st_mode & 0o777 == 0o700
        (root / 'xet').mkdir(mode=0o700)
        (root / 'xet' / 'interrupted-state').write_bytes(b'private temporary state')
        child = await original(sys.executable, '-c', 'import time; time.sleep(90)', **kwargs)
        wait = child.wait
        async def slow_wait():
            code = await wait()
            reaping.set()
            await release.wait()
            return code
        child.wait = slow_wait
        children.append(child)
        return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    task = asyncio.create_task(broker._invoke({}, check_fence=broker._noop))
    while not children: await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.wait_for(reaping.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    assert roots[0].exists()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert children[0].returncode == -signal.SIGKILL
    assert not roots[0].exists()
