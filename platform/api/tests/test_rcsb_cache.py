"""RCSB imports and cache writes use mutable storage, not the checkout."""
import asyncio
import importlib


def test_rcsb_cache_is_external_and_created_only_on_write(tmp_path, monkeypatch):
    import paths
    cache = tmp_path / "analysis_cache"
    monkeypatch.setattr(paths, "get_analysis_cache_dir", lambda: cache)
    from routers import rcsb
    importlib.reload(rcsb)
    try:
        assert rcsb.RCSB_CACHE_DIR == cache / "rcsb"
        assert not cache.exists()
        assert asyncio.run(rcsb.list_cached())["cached"] == []

        class Response:
            status_code = 200
            content = b"REMARK offline cache fixture\nEND\n"
            def raise_for_status(self):
                pass

        class Client:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def get(self, url):
                return Response()

        monkeypatch.setattr(rcsb.httpx, "AsyncClient", Client)
        result = asyncio.run(rcsb.fetch_pdb("1ABC"))
        assert result["path"] == "analysis_cache/rcsb/1abc.pdb"
        assert paths.resolve_allowed_path(result["path"]).read_bytes() == Response.content
        assert asyncio.run(rcsb.fetch_pdb("1ABC"))["cached"] is True
        assert asyncio.run(rcsb.list_cached())["count"] == 1
        assert asyncio.run(rcsb.delete_cached("1ABC"))["success"]
    finally:
        monkeypatch.undo()
        importlib.reload(rcsb)
