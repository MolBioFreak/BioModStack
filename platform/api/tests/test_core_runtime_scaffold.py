from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_core_runtime_scaffold_files_exist() -> None:
    expected_paths = [
        "compose.core-runtime.yml",
        ".env.core-runtime.example",
        ".dockerignore",
        "docker/api.Dockerfile",
        "docker/web.Dockerfile",
        "docker/web/nginx.conf",
        "scripts/run_biomodstack_core_runtime.sh",
    ]

    missing = [path for path in expected_paths if not (REPO_ROOT / path).exists()]
    assert missing == []


def test_compose_core_runtime_contract() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"bms-api", "bms-web"}

    api = compose["services"]["bms-api"]
    assert api["build"]["dockerfile"] == "docker/api.Dockerfile"
    assert api["container_name"] == "biomodstack-api"
    assert api["ports"] == ["127.0.0.1:${BMS_API_HOST_PORT:-8000}:8000"]
    assert api["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert api["environment"]["BMS_HOME"] == "/app"
    assert api["environment"]["BMS_CORE_RUNTIME_MODE"] == "${BMS_CORE_RUNTIME_MODE:-1}"
    assert api["environment"]["BMS_WORKFLOW_ADAPTER_URL"] == "${BMS_WORKFLOW_ADAPTER_URL:-}"
    assert "BIOXP_SERVER_URL" in api["environment"]

    web = compose["services"]["bms-web"]
    assert web["build"]["dockerfile"] == "docker/web.Dockerfile"
    assert web["container_name"] == "biomodstack-web"
    assert web["ports"] == ["127.0.0.1:${BMS_WEB_HOST_PORT:-5173}:80"]
    assert web["depends_on"]["bms-api"]["condition"] == "service_healthy"


def test_nginx_contract_preserves_bms_and_api_routes() -> None:
    nginx_conf = (REPO_ROOT / "docker" / "web" / "nginx.conf").read_text(encoding="utf-8")

    assert "absolute_redirect off;" in nginx_conf
    assert "location = / {" in nginx_conf
    assert "return 302 /bms/;" in nginx_conf
    assert "location /bms/ {" in nginx_conf
    assert "try_files $uri $uri/ /bms/index.html;" in nginx_conf
    assert "location /api/ {" in nginx_conf
    assert "proxy_pass http://bms-api:8000;" in nginx_conf


def test_dockerignore_keeps_local_runtime_state_out_of_images() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    for required in [".git", ".venv", "platform/frontend/node_modules", "work", "bms_results", "analysis_cache", "*.db"]:
        assert required in dockerignore


def test_vite_config_uses_reproducible_stable_pdbe_alias_and_browser_safe_buffer_resolution() -> None:
    vite_config = (REPO_ROOT / "platform" / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    frontend_package = (REPO_ROOT / "platform" / "frontend" / "package.json").read_text(encoding="utf-8")

    assert "require.resolve('pdbe-molstar-stable/package.json')" in vite_config
    assert "node_modules/.ignored/pdbe-molstar" not in vite_config
    assert '"safe-buffer":' in frontend_package
    assert "safe-buffer" in vite_config
    assert "node_modules/safe-buffer/index.js" in vite_config


def test_frontend_router_uses_vite_base_url_for_subpath_deployments() -> None:
    main_tsx = (REPO_ROOT / "platform" / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "getRouterBasename({ envBaseUrl: import.meta.env.BASE_URL })" in main_tsx
    assert "basename={routerBasename}" in main_tsx
    assert "isAppPath(window.location.pathname, '/designer', routerBasename)" in main_tsx


def test_core_runtime_env_example_documents_transition_knobs() -> None:
    env_example = (REPO_ROOT / ".env.core-runtime.example").read_text(encoding="utf-8")

    for required in [
        "BMS_STATE_DIR=",
        "BMS_CONTAINER_STATE_PATH=",
        "BMS_API_HOST_PORT=8000",
        "BMS_WEB_HOST_PORT=5173",
        "BMS_CORE_RUNTIME_MODE=1",
        "BMS_WORKFLOW_ADAPTER_URL=",
        "BIOXP_SERVER_URL=",
    ]:
        assert required in env_example


def test_core_runtime_script_loads_repo_local_env_overrides() -> None:
    runtime_script = (REPO_ROOT / "scripts" / "run_biomodstack_core_runtime.sh").read_text(encoding="utf-8")

    assert ".env.core-runtime.local" in runtime_script
    assert "BMS_CORE_RUNTIME_ENV_FILE" in runtime_script
    assert "--env-file" in runtime_script
