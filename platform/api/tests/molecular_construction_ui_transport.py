"""Inert stdin/stdout ASGI transport for mounted graph workbench tests.

No server, main app, stores, network sockets or inference. Every response is from
an actual route with the exact serialized Axios request body.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def main():
    request = json.load(sys.stdin)
    spec = importlib.util.spec_from_file_location(
        "construction_ui_router", Path(__file__).parents[1] / "routers/molecular_construction.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://inert") as client:
        response = await client.request(request["method"], request["url"], content=request.get("body"), headers={"content-type": "application/json"})
        print(json.dumps({"status": response.status_code, "data": response.json()}))


if __name__ == "__main__":
    asyncio.run(main())
