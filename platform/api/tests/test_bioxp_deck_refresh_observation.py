"""Query-only observer consumes distinct producer freshness, not readiness."""
import asyncio
import copy
from collections.abc import Awaitable, Callable
from ipaddress import ip_address

import httpx
import pytest

from services.bioxp.robot_client import BioXpRobotClient, _hardware_evidence_needs_refresh
from services.bioxp.target_policy import ValidatedBioXpTarget
from tests.test_bioxp_connection import _load, _service
from tests.test_bioxp_manual_readiness_schedule import payload


@pytest.mark.parametrize("field", ["admission_observation", "deck_authority"])
@pytest.mark.parametrize("state", ["missing", "stale"])
def test_partial_invalidation_is_not_hidden_by_fresh_generic_status(field, state):
    row = payload(0)
    row[field]["available"] = False
    row[field]["freshness"]["state"] = state
    before = copy.deepcopy(row)
    assert _hardware_evidence_needs_refresh(row)
    assert row == before


@pytest.mark.parametrize("field", ["admission_observation", "deck_authority"])
@pytest.mark.parametrize("invalid", [None, True, -1, float("nan"), float("inf")])
def test_invalid_source_age_never_postpones_collection(field, invalid):
    row = payload(0)
    row[field]["freshness"]["age_s"] = invalid
    assert _hardware_evidence_needs_refresh(row)


@pytest.mark.parametrize("field", ["admission_observation", "deck_authority"])
def test_missing_projection_requires_observation_not_generic_cache(field):
    row = payload(0)
    del row[field]
    assert _hardware_evidence_needs_refresh(row)


def test_source_budget_not_guessed_ttl_controls_refresh():
    row = payload(2)
    row["deck_authority"]["freshness"]["fresh_for_s"] = 4
    assert _hardware_evidence_needs_refresh(row)
    row["deck_authority"]["freshness"]["fresh_for_s"] = 8
    assert not _hardware_evidence_needs_refresh(row)
    row["admission_observation"]["freshness"]["fresh_for_s"] = 3
    assert _hardware_evidence_needs_refresh(row)


def test_fresh_negative_and_park_only_denial_do_not_requery():
    row = payload(0)
    row["deck_authority"]["available"] = False
    row["deck_authority"]["reason"] = "references_unavailable"
    assert not _hardware_evidence_needs_refresh(row)
    row["deck_authority"]["available"] = True
    row["deck_authority"]["park"] = {"available": False, "reason": "tip_unknown"}
    assert not _hardware_evidence_needs_refresh(row)
    row["deck_authority"]["freshness"]["age_s"] = 7.5
    assert _hardware_evidence_needs_refresh(row)


@pytest.mark.parametrize("runtime_ready,capabilities", [(False, ["collect_hardware_snapshot"]), (True, [])])
def test_runtime_capability_gate_unchanged(runtime_ready, capabilities):
    row = payload(40)
    row.update(runtime_ready=runtime_ready, capabilities=capabilities)
    assert not _hardware_evidence_needs_refresh(row)


@pytest.mark.parametrize("negative", [False, True])
def test_real_worker_prompt_postcommand_query_and_no_poll_collection_storm(tmp_path, monkeypatch, negative):
    """Real service loop and client transport, only physical/network leaves doubled."""
    _, Profile, _, _ = _load()
    clients = []
    now = [0.0]
    collected_at = [0.0]
    invalidated = [False]
    posts = []
    requests = []

    async def handle(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.url.path == "/hardware/snapshot/collect"
            assert request.content == b'{"automatic":true}'
            posts.append(now[0])
            collected_at[0] = now[0]
            invalidated[0] = False
            return httpx.Response(200, json={"ok": True, "published": True,
                "snapshot": {"snapshot_id": "after-command"}})
        assert request.url.path == "/status"
        row = payload(now[0] - collected_at[0])
        row["freshness"]["age_s"] = 0  # unrelated generic domains remain fresh
        row["deck_authority"]["available"] = not negative
        if negative:
            row["deck_authority"]["reason"] = "references_unavailable"
        else:
            row["deck_authority"]["park"] = {"available": False}
        if invalidated[0]:
            row["admission_observation"].update(available=False, cache_state="missing")
            row["admission_observation"]["freshness"]["state"] = "missing"
            row["deck_authority"]["freshness"]["state"] = "missing"
        return httpx.Response(200, json=row)

    async def scenario():
        service = _service(tmp_path, clients, active_probe_interval_seconds=10)
        service.client_factory = lambda target: BioXpRobotClient(target,
            transport=httpx.MockTransport(handle), monotonic_clock=lambda: now[0])
        await service.save_profile(Profile(api_url="http://robot:8123"))
        await service.connect()
        assert requests == [("GET", "/status")]
        service._stop_active_probe_locked()
        service._stop_snapshot_refresh_locked()
        # Finish cancellation before replacing sleep in this deterministic worker run.
        await asyncio.sleep(0)
        invalidated[0] = True  # source-side command completion invalidation
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
            now[0] += delay
            if now[0] > 10:
                raise asyncio.CancelledError
        try:
            with monkeypatch.context() as m:
                m.setattr(asyncio, "sleep", sleep)
                with pytest.raises(asyncio.CancelledError):
                    await service._snapshot_refresh_loop()
            assert posts == [1.0, 8.5]
            assert max(sleeps) <= 1.0
            assert all(path in {"/status", "/hardware/snapshot/collect"} for _, path in requests)
            # Observation freshness does not change source-owned availability.
            assert _hardware_evidence_needs_refresh(payload(0)) is False
        finally:
            await service.disconnect()
        assert service._snapshot_refresh_task is None
        assert service._active_probe_task is None
    asyncio.run(scenario())


def test_failed_collection_retains_backoff_with_one_second_worker(tmp_path):
    target = ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http", hostname="robot",
        port=8123, resolved_addresses=(ip_address("100.64.0.10"),))
    now = [0.0]
    posts = []
    async def handle(request):
        if request.method == "POST":
            posts.append(now[0])
            return httpx.Response(503, json={"detail": "failed observation"})
        return httpx.Response(200, json=payload(40))
    async def scenario():
        client = BioXpRobotClient(target, transport=httpx.MockTransport(handle), monotonic_clock=lambda: now[0])
        try:
            for tick in range(32):
                now[0] = float(tick)
                await client.probe()
            assert posts == [0.0, 30.0]
        finally:
            await client.close()
    asyncio.run(scenario())

def test_foreground_arrival_during_duration_scheduled_worker_yields(tmp_path):
    _, Profile, _, _ = _load()
    transport = DurationTransport()
    async def scenario():
        service = _service(tmp_path, [], active_probe_interval_seconds=10)
        service.client_factory = lambda target: BioXpRobotClient(target,
            transport=httpx.MockTransport(transport.handle), monotonic_clock=lambda: transport.now)
        await service.save_profile(Profile(api_url="http://robot:8123"))
        await service.connect()
        service._stop_active_probe_locked()
        service._stop_snapshot_refresh_locked()
        await asyncio.sleep(0)
        try:
            await service._snapshot_refresh_once()
            transport.now += 2
            entered, release = asyncio.Event(), asyncio.Event()
            async def admission_wait():
                entered.set()
                await release.wait()
            transport.on_collect = admission_wait
            pending = asyncio.create_task(service._snapshot_refresh_once())
            await asyncio.wait_for(entered.wait(), timeout=1)
            transport.mode = "foreground"  # arrives after GET/queued POST
            release.set()
            assert await pending == .5
            assert isinstance(service._client, BioXpRobotClient)
            assert service._client._snapshot_acquisition_seconds == 12
            assert len(transport.finished) == 1
            assert transport.status()["deck_authority"]["available"] is True
            transport.now += .49
            await service._snapshot_refresh_once()
            assert len(transport.posts) == 2
            transport.now += .01
            transport.mode = "success"
            await service._snapshot_refresh_once()
            assert len(transport.posts) == 3
            assert len(transport.finished) == 2
            assert transport.finished[1] < transport.finished[0] + 15
        finally:
            await service.disconnect()
    asyncio.run(scenario())


class DurationTransport:
    """Deterministic monotonic time advances INSIDE awaited HTTP collection.

    Producer TTLs remain 15/30s; old samples survive replacement until their
    real simulated expiry. No measurement is injected into the client.
    """
    def __init__(self, *, duration=12.0, negative=False):
        self.now = 0.0
        self.duration = duration
        self.negative = negative
        self.deck_at = None
        self.hardware_at = None
        self.posts = []
        self.finished = []
        self.during = []
        self.requests = []
        self.mode = "success"
        self.status_latency = 0.0
        self.yield_task = asyncio.sleep
        self.on_collect: Callable[[], Awaitable[None]] | None = None

    def status(self):
        row = payload(0)
        for key, at, budget in (("admission_observation", self.hardware_at, 30.0),
                                ("deck_authority", self.deck_at, 15.0)):
            age = None if at is None else self.now - at
            fresh = age is not None and age < budget
            row[key]["available"] = fresh and not self.negative
            row[key]["cache_state"] = "fresh" if fresh else "missing" if at is None else "stale"
            row[key]["freshness"] = {"age_s": age, "fresh_for_s": budget,
                "state": "fresh" if fresh else "missing" if at is None else "stale"}
        row["deck_authority"]["outcome"] = "failed" if self.negative else "observed"
        row.update(copy.deepcopy(row["admission_observation"]))
        return row

    async def handle(self, request):
        self.requests.append((request.method, request.url.path))
        if request.method == "GET":
            assert request.url.path == "/status"
            self.now += self.status_latency
            if self.mode == "readback_error" and len(self.finished) == 2:
                return httpx.Response(503, json={"detail": "status unavailable"})
            return httpx.Response(200, json=self.status())
        assert request.url.path == "/hardware/snapshot/collect"
        assert request.content == b'{"automatic":true}'
        self.posts.append(self.now)
        previous = self.deck_at
        if self.on_collect is not None:
            await self.on_collect()
        if self.mode == "foreground":
            return httpx.Response(200, json={"published": False, "reason": "operator_action_pending"})
        # Physical hardware subphase finishes early; full collection includes
        # later deck/admission/transport work. Measure the WHOLE awaited flow.
        hardware_at = self.now + self.duration / 3
        for _ in range(12):
            self.now += self.duration / 12
            self.during.append((len(self.posts), self.now, previous,
                                self.status()["deck_authority"]["available"]))
            await self.yield_task(0)
        if self.mode == "error":
            return httpx.Response(503, json={"detail": "query failed"})
        if self.mode == "incomplete":
            return httpx.Response(200, json={"ok": True, "published": False})
        self.deck_at = self.now
        self.hardware_at = hardware_at
        self.finished.append(self.now)
        return httpx.Response(200, json={"ok": True, "published": True,
            "snapshot": {"snapshot_id": f"full-{len(self.posts)}"}})


def duration_client(transport):
    target = ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http", hostname="robot",
        port=8123, resolved_addresses=(ip_address("100.64.0.10"),))
    return BioXpRobotClient(target, transport=httpx.MockTransport(transport.handle),
                           monotonic_clock=lambda: transport.now)


@pytest.mark.parametrize("duration", [12.0, 0.12])
@pytest.mark.parametrize("negative", [False, True])
def test_full_duration_worker_replaces_before_real_deck_expiry(tmp_path, monkeypatch, record_property, duration, negative):
    import json
    transport = DurationTransport(duration=duration, negative=negative)
    _, Profile, _, _ = _load()

    async def scenario():
        service = _service(tmp_path, [], active_probe_interval_seconds=10)
        service.client_factory = lambda target: BioXpRobotClient(target,
            transport=httpx.MockTransport(transport.handle), monotonic_clock=lambda: transport.now)
        await service.save_profile(Profile(api_url="http://robot:8123"))
        await service.connect()
        assert transport.posts == []  # connect stays passive
        service._stop_active_probe_locked()
        service._stop_snapshot_refresh_locked()
        await asyncio.sleep(0)
        sleeps = []
        async def sleep(delay):
            if len(transport.finished) == 2:
                raise asyncio.CancelledError
            sleeps.append(delay)
            transport.now += delay
        try:
            with monkeypatch.context() as m:
                m.setattr(asyncio, "sleep", sleep)
                with pytest.raises(asyncio.CancelledError):
                    await service._snapshot_refresh_loop()
            record_property("duration_trace", json.dumps({"posts": transport.posts,
                "finished": transport.finished, "during": transport.during, "sleeps": sleeps}))
            assert transport.posts[0] == 1.0  # cold sample, not a guessed duration
            assert len(transport.posts) == 2
            assert max(sleeps) <= 1.0
            assert transport.finished[1] < transport.finished[0] + 15.0
            if duration < 1:
                assert transport.posts[1] - transport.finished[0] == pytest.approx(7.5)
            else:
                assert transport.posts[1] - transport.finished[0] <= 3.0
            for number, at, previous, available in transport.during:
                if number == 2:
                    assert available is (not negative and at < previous + 15)
            assert transport.status()["deck_authority"]["available"] is not negative
        finally:
            await service.disconnect()
        assert service._snapshot_refresh_task is None
    asyncio.run(scenario())


def test_measured_duration_includes_both_status_transports():
    transport = DurationTransport()
    transport.status_latency = 0.25
    async def scenario():
        client = duration_client(transport)
        try:
            await client.probe()
            assert client._snapshot_acquisition_seconds == pytest.approx(12.5)
            assert transport.status()["deck_authority"]["freshness"]["age_s"] == .25
            transport.now += 1.25
            await client.probe()
            assert len(transport.posts) == 2
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["error", "incomplete", "foreground", "readback_error"])
def test_duration_measurement_survives_unsuccessful_attempt_and_backoff(mode):
    transport = DurationTransport()
    async def scenario():
        client = duration_client(transport)
        try:
            await client.probe()
            assert client._snapshot_acquisition_seconds == 12
            transport.now += 2
            transport.mode = mode
            row = await client.probe()
            assert row["automatic_snapshot_refresh"]["published"] is False
            assert len(transport.posts) == 2
            assert client._snapshot_acquisition_seconds == 12
            delay = .5 if mode == "foreground" else 30
            assert client._snapshot_retry_after == transport.now + delay
            if mode == "readback_error":
                # Subsequent status recovers; failed full refresh still backs off.
                transport.mode = "success"
                transport.deck_at = None
            transport.now += delay - .01
            row = await client.probe()
            assert row["automatic_snapshot_refresh"]["retry_deferred"] is True
            assert len(transport.posts) == 2
            transport.now += .02
            transport.mode = "success"
            await client.probe()
            assert len(transport.posts) == 3
        finally:
            await client.close()
    asyncio.run(scenario())


def test_actual_duration_overrun_cannot_extend_old_deck_authority(record_property):
    import json
    transport = DurationTransport()
    async def scenario():
        client = duration_client(transport)
        try:
            await client.probe()
            transport.now += 2
            transport.duration = 18  # unpredicted slowdown is not stale permission
            await client.probe()
            samples = [r for r in transport.during if r[0] == 2]
            record_property("overrun_trace", json.dumps(samples))
            assert any(available for _, _, _, available in samples)
            assert any(not available for _, _, _, available in samples)
            for _, at, previous, available in samples:
                assert available is (at < previous + 15)
            assert client._snapshot_acquisition_seconds == 18
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("new_url", ["http://robot:8123", "http://100.64.0.11:8123"])
def test_generation_target_replacement_does_not_borrow_duration_or_evidence(tmp_path, new_url):
    _, Profile, _, _ = _load()
    old = DurationTransport()
    new = DurationTransport(duration=.12)
    clients = []
    async def scenario():
        service = _service(tmp_path, [], active_probe_interval_seconds=10)
        def factory(target):
            transport = old if not clients else new
            client = BioXpRobotClient(target, transport=httpx.MockTransport(transport.handle),
                monotonic_clock=lambda: transport.now)
            clients.append(client)
            return client
        service.client_factory = factory
        await service.save_profile(Profile(api_url="http://robot:8123"))
        await service.connect()
        service._stop_active_probe_locked()
        service._stop_snapshot_refresh_locked()
        await asyncio.sleep(0)
        await service._snapshot_refresh_once()
        assert clients[0]._snapshot_acquisition_seconds == 12
        generation = service.snapshot().generation
        old.now += 2
        entered, release = asyncio.Event(), asyncio.Event()
        async def block():
            entered.set()
            await release.wait()
        old.on_collect = block
        pending = asyncio.create_task(service._snapshot_refresh_once())
        await entered.wait()
        try:
            await service.save_profile(Profile(api_url=new_url))
            await service.connect()
            service._stop_active_probe_locked()
            service._stop_snapshot_refresh_locked()
            assert service.snapshot().generation > generation
            assert clients[1]._snapshot_acquisition_seconds is None
            assert new.posts == []
            assert service.snapshot().hardware_observation_fresh is not True
            release.set()
            await pending
            assert service._client is clients[1]
            assert service.snapshot().hardware_observation_fresh is not True
            await service._snapshot_refresh_once()
            assert len(new.posts) == 1
            assert clients[1]._snapshot_acquisition_seconds == pytest.approx(.12)
        finally:
            release.set()
            await pending
            await service.disconnect()
    asyncio.run(scenario())

def test_real_monotonic_worker_with_twelve_second_http_collection(tmp_path, record_property):
    """Wall-clock acceptance: no fake clock, sleep patch or injected cost."""
    import json
    import time
    _, Profile, _, _ = _load()
    started = time.monotonic()
    deck_at: list[float | None] = [None]
    posts, finished, during = [], [], []
    complete = None

    def status():
        now = time.monotonic()
        age = None if deck_at[0] is None else now - deck_at[0]
        row = payload(age or 0)
        if age is None:
            for key in ("admission_observation", "deck_authority"):
                row[key]["available"] = False
                row[key]["cache_state"] = "missing"
                row[key]["freshness"].update(state="missing", age_s=None)
        elif age >= 15:
            row["deck_authority"]["available"] = False
            row["deck_authority"]["freshness"]["state"] = "stale"
        return row

    async def handle(request):
        if request.method == "GET":
            assert request.url.path == "/status"
            return httpx.Response(200, json=status())
        assert request.url.path == "/hardware/snapshot/collect"
        assert request.content == b'{"automatic":true}'
        posts.append(time.monotonic() - started)
        for _ in range(12):
            await asyncio.sleep(1.0)  # real time inside successful HTTP collection
            during.append((len(posts), time.monotonic() - started,
                           status()["deck_authority"]["available"]))
        deck_at[0] = time.monotonic()
        finished.append(deck_at[0] - started)
        if len(finished) == 2:
            assert complete is not None
            complete.set()
        return httpx.Response(200, json={"ok": True, "published": True,
            "snapshot": {"snapshot_id": f"wall-{len(finished)}"}})

    async def scenario():
        nonlocal complete
        complete = asyncio.Event()
        service = _service(tmp_path, [], active_probe_interval_seconds=10)
        service.client_factory = lambda target: BioXpRobotClient(target, transport=httpx.MockTransport(handle))
        await service.save_profile(Profile(api_url="http://robot:8123"))
        await service.connect()
        assert posts == []
        service._stop_active_probe_locked()  # keep the genuine single refresh worker
        try:
            await asyncio.wait_for(complete.wait(), timeout=45)
            async with service._probe_lock:
                pass  # final readback/publication has left the probe lane
            record_property("wallclock_trace", json.dumps({"posts": posts, "finished": finished,
                "during": during, "deadline": finished[0] + 15}))
            assert len(posts) == 2
            assert all(end - begin >= 12 for begin, end in zip(posts, finished))
            assert finished[1] < finished[0] + 15
            assert all(available for number, _, available in during if number == 2)
        finally:
            await service.disconnect()
        assert service._snapshot_refresh_task is None
    asyncio.run(scenario())
