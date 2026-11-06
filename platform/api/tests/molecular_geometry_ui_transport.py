"""Inert exact Axios payload replay; no live application or sockets."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

async def main():
    request = json.load(sys.stdin)
    app = FastAPI()
    for name in ('molecular_construction', 'molecular_geometry'):
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / f'routers/{name}.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        app.include_router(module.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://inert') as client:
        response = await client.request(request['method'], request['url'], content=request.get('body'), headers={'content-type': 'application/json'})
        print(json.dumps({'status': response.status_code, 'data': response.json()}))

if __name__ == '__main__':
    asyncio.run(main())
