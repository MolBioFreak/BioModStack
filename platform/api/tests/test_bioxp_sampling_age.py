"""Actual collection-start sample ages, not timestamp renewal at return."""
import asyncio
import httpx
from services.bioxp.robot_client import BioXpRobotClient
from test_bioxp_deck_refresh_observation import payload, _load, _service

def test_sample_captured_before_full_collection_return_does_not_expire(tmp_path, record_property):
    """Wall-clock acceptance: no fake clock, sleep patch or injected cost."""
    import json
    import time
    _, Profile, _, _ = _load()
    started = time.monotonic()
    deck_at: list[float | None] = [None]
    captures = []
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
        sampled_at = None
        for step in range(24):
            await asyncio.sleep(0.5)  # real awaited controller subphase
            if step == 18:
                sampled_at = time.monotonic()  # sample begins before its validation ends
            during.append((len(posts), time.monotonic() - started,
                           status()["deck_authority"]["available"]))
        assert sampled_at is not None
        deck_at[0] = sampled_at
        captures.append(sampled_at - started)
        finished.append(time.monotonic() - started)
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
                "during": during, "captures": captures, "deadline": captures[0] + 15}))
            assert len(posts) == 2
            assert all(end - begin >= 12 for begin, end in zip(posts, finished))
            assert finished[1] < captures[0] + 15
            assert all(end - sample >= 2.5 for end, sample in zip(finished, captures))
            assert all(available for number, _, available in during if number == 2)
        finally:
            await service.disconnect()
        assert service._snapshot_refresh_task is None
    asyncio.run(scenario())


def test_fast_or_missing_success_never_requests_zero_delay_spin(tmp_path):
    from test_bioxp_deck_refresh_observation import DurationTransport, duration_client

    async def scenario():
        for mode in ('missing', 'short_budget', 'negative'):
            transport = DurationTransport(duration=.12, negative=mode == 'negative')
            original_status = transport.status
            def status():
                row = original_status()
                for key in ('admission_observation', 'deck_authority'):
                    if mode == 'missing':
                        row[key]['freshness'].update(state='missing', age_s=None)
                        row[key]['available'] = False
                    elif mode == 'short_budget':
                        row[key]['freshness']['fresh_for_s'] = .1
                return row
            transport.status = status
            client = duration_client(transport)
            try:
                row = await client.probe()
                assert row['automatic_snapshot_refresh']['published'] is True
                assert row['automatic_snapshot_refresh'].get('next_probe_after_s') != 0
                service = _service(tmp_path / mode, [])
                service.snapshot_refresh_interval_seconds = 1
                assert service._snapshot_refresh_delay(row) == 1
                assert len(transport.posts) == 1
            finally:
                await client.close()
    asyncio.run(scenario())
