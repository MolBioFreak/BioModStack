"""Tools screen API: a thin layer over the tool installer engine.

Reads are open to any client of the API. Anything that builds, adopts, removes
or changes the selection is limited to local requests, like the other
system-admin routes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import biomodstack_tools as engine  # noqa: E402

router = APIRouter(prefix="/api/tools", tags=["tools"])

LOCAL_ADMIN_HOSTS = {None, "127.0.0.1", "::1", "localhost", "testclient"}


class ModulesPayload(BaseModel):
    modules: list[str]


class BuildPayload(BaseModel):
    accept_licenses: list[str] = []
    weights: list[str] = []
    skip_weights: bool = False
    replace: bool = False


class AdoptPayload(BaseModel):
    path: str
    image: str | None = None
    copy_image: bool = False


def _require_local(request: Request) -> None:
    if request.client and request.client.host not in LOCAL_ADMIN_HOSTS:
        raise HTTPException(status_code=403, detail="Tool installation is limited to local requests")


def _blocked(exc: engine.ToolError) -> HTTPException:
    status = 404 if exc.reason_code == "unknown_tool" else 409
    return HTTPException(status_code=status, detail={"reason_code": exc.reason_code, "message": exc.message})


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except engine.ToolError as exc:
        raise _blocked(exc) from exc


def _overview() -> dict:
    where = engine.locations(REPO_ROOT)
    recipes = engine.load_recipes(REPO_ROOT)
    tools = []
    for recipe in recipes.values():
        row = engine.tool_status(recipe, where, REPO_ROOT)
        row["upstream"] = recipe["upstream"]
        row["license"] = recipe.get("license")
        row["notes"] = recipe.get("notes")
        tools.append(row)
    return {**engine.modules_overview(where, REPO_ROOT), "tools": tools,
            "container_runtime_present": engine._apptainer() is not None}


@router.get("")
async def list_tools():
    return await run_in_threadpool(_call, _overview)


@router.put("/modules")
async def put_modules(request: Request, payload: ModulesPayload):
    _require_local(request)

    def work():
        engine.select_modules(engine.locations(REPO_ROOT), payload.modules)
        return _overview()
    return await run_in_threadpool(_call, work)


def _recipe_and_where(tool: str):
    return engine.get_recipe(tool, REPO_ROOT), engine.locations(REPO_ROOT)


@router.get("/{tool}/plan")
async def get_plan(tool: str):
    def work():
        recipe, where = _recipe_and_where(tool)
        return engine.tool_plan(recipe, where, REPO_ROOT)
    return await run_in_threadpool(_call, work)


@router.get("/{tool}/status")
async def get_status(tool: str):
    def work():
        recipe, where = _recipe_and_where(tool)
        return engine.tool_status(recipe, where, REPO_ROOT)
    return await run_in_threadpool(_call, work)


@router.get("/{tool}/log")
async def get_log(tool: str, tail: int = 200):
    def work():
        recipe, where = _recipe_and_where(tool)
        path = where["state_dir"] / recipe["id"] / "build.log"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
        return {"tool": recipe["id"], "log_path": str(path), "lines": lines[-max(1, min(tail, 2000)):]}
    return await run_in_threadpool(_call, work)


@router.post("/{tool}/build")
async def post_build(request: Request, tool: str, payload: BuildPayload):
    """Start a background build through the same command a person would type."""
    _require_local(request)

    def work():
        engine.get_recipe(tool, REPO_ROOT)
        argv = [sys.executable, "-B", str(REPO_ROOT / "biomodstack_tools.py"), "build", tool, "--detach", "--json"]
        for license_id in payload.accept_licenses:
            argv += ["--accept-license", license_id]
        for weight in payload.weights:
            argv += ["--weights", weight]
        if payload.skip_weights:
            argv.append("--skip-weights")
        if payload.replace:
            argv.append("--replace")
        done = subprocess.run(argv, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60, check=False)
        try:
            document = json.loads(done.stdout)
        except ValueError as exc:
            raise engine.ToolError("build_start_failed", (done.stderr or done.stdout)[-400:]) from exc
        if done.returncode != 0:
            raise engine.ToolError(document.get("reason_code") or "build_start_failed",
                                   document.get("message") or "The build could not be started.")
        return document
    return await run_in_threadpool(_call, work)


@router.post("/{tool}/verify")
async def post_verify(request: Request, tool: str):
    _require_local(request)

    def work():
        recipe, where = _recipe_and_where(tool)
        return engine.verify_tool(recipe, where, REPO_ROOT)
    return await run_in_threadpool(_call, work)


@router.post("/{tool}/adopt")
async def post_adopt(request: Request, tool: str, payload: AdoptPayload):
    _require_local(request)

    def work():
        recipe, where = _recipe_and_where(tool)
        return engine.adopt_tool(recipe, where, Path(payload.path), REPO_ROOT,
                                 image_name=payload.image, copy=payload.copy_image)
    return await run_in_threadpool(_call, work)


@router.post("/{tool}/remove")
async def post_remove(request: Request, tool: str):
    _require_local(request)

    def work():
        recipe, where = _recipe_and_where(tool)
        return engine.remove_tool(recipe, where, REPO_ROOT)
    return await run_in_threadpool(_call, work)
