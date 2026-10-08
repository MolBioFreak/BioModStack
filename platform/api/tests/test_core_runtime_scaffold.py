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
        "scripts/run_biomodstack_workflow_adapter.sh",
    ]

    missing = [path for path in expected_paths if not (REPO_ROOT / path).exists()]
    assert missing == []


def test_compose_core_runtime_contract() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"bms-api", "bms-cpu-power", "bms-web"}

    api = compose["services"]["bms-api"]
    assert api["build"]["dockerfile"] == "docker/api.Dockerfile"
    assert api["container_name"] == "biomodstack-api"
    assert api["network_mode"] == "host"
    assert "ports" not in api
    assert "extra_hosts" not in api
    assert api["environment"]["BMS_HOME"] == "/app"
    assert api["environment"]["BMS_CORE_RUNTIME_MODE"] == "${BMS_CORE_RUNTIME_MODE:-1}"
    assert api["environment"]["BMS_WORKFLOW_ADAPTER_URL"] == "${BMS_WORKFLOW_ADAPTER_URL:-http://127.0.0.1:8001}"
    assert api["environment"]["BMS_CPU_POWER_COLLECTOR_URL"] == "${BMS_CPU_POWER_COLLECTOR_URL:-http://127.0.0.1:8797/power}"
    assert api["environment"]["CORS_ORIGINS"] == "${CORS_ORIGINS:-http://127.0.0.1,http://127.0.0.1:5173,http://127.0.0.1:18080,http://localhost,https://localhost,http://localhost:5173,http://localhost:18080,https://localhost:5173,https://127.0.0.1}"
    assert api["environment"]["BMS_WEIGHTS"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/weights"
    assert api["environment"]["BMS_COLABFOLD_DB"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/colabfold_db"
    assert api["environment"]["BMS_MSA_CACHE"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/msa_cache"
    assert api["environment"]["BMS_SABDAB_CACHE"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/sabdab_cache"
    assert "BIOXP_SERVER_URL" in api["environment"]

    cpu_power = compose["services"]["bms-cpu-power"]
    assert cpu_power["build"]["dockerfile"] == "docker/api.Dockerfile"
    assert cpu_power["container_name"] == "biomodstack-cpu-power"
    assert cpu_power["network_mode"] == "host"
    assert cpu_power["user"] == "0:0"
    assert cpu_power["environment"]["BMS_POWER_CAP_ROOT"] == "/host_sys/class/powercap"
    assert cpu_power["environment"]["BMS_CPU_POWER_BIND_HOST"] == "127.0.0.1"
    assert cpu_power["environment"]["BMS_CPU_POWER_PORT"] == "${BMS_CPU_POWER_PORT:-8797}"
    assert cpu_power["command"] == ["python", "/app/platform/api/tools/cpu_power_collector.py"]
    assert cpu_power["volumes"][0]["source"] == "/sys"
    assert cpu_power["volumes"][0]["read_only"] is True

    web = compose["services"]["bms-web"]
    assert web["build"]["dockerfile"] == "docker/web.Dockerfile"
    assert web["container_name"] == "biomodstack-web"
    assert web["network_mode"] == "host"
    assert "ports" not in web
    assert web["depends_on"]["bms-api"]["condition"] == "service_healthy"
    assert web["environment"]["BMS_WEB_HOST_PORT"] == "${BMS_WEB_HOST_PORT:-18080}"
    assert web["healthcheck"]["test"] == ["CMD-SHELL", 'wget -qO- "http://127.0.0.1:${BMS_WEB_HOST_PORT:-18080}/bms/"']


def test_nginx_contract_preserves_bms_and_api_routes() -> None:
    nginx_conf = (REPO_ROOT / "docker" / "web" / "nginx.conf").read_text(encoding="utf-8")

    assert "listen 127.0.0.1:${BMS_WEB_HOST_PORT};" in nginx_conf
    assert "absolute_redirect off;" in nginx_conf
    assert "location = / {" in nginx_conf
    assert "return 302 /bms/;" in nginx_conf
    assert "location /bms/ {" in nginx_conf
    assert "try_files $uri $uri/ /bms/index.html;" in nginx_conf
    assert "location /api/ {" in nginx_conf
    assert "proxy_pass http://127.0.0.1:8000;" in nginx_conf


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
        "BMS_WEB_HOST_PORT=18080",
        "CORS_ORIGINS=http://127.0.0.1,http://127.0.0.1:5173,http://127.0.0.1:18080,http://localhost,https://localhost,http://localhost:5173,http://localhost:18080,https://localhost:5173,https://127.0.0.1",
        "BMS_CORE_RUNTIME_MODE=1",
        "BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:8001",
        "BIOXP_SERVER_URL=",
    ]:
        assert required in env_example
    assert "BMS_API_HOST_PORT" not in env_example


def test_core_runtime_script_loads_repo_local_env_overrides() -> None:
    runtime_script = (REPO_ROOT / "scripts" / "run_biomodstack_core_runtime.sh").read_text(encoding="utf-8")

    assert ".env.core-runtime.local" in runtime_script
    assert "BMS_CORE_RUNTIME_ENV_FILE" in runtime_script
    assert "--env-file" in runtime_script
    assert 'BMS_WEB_HOST_PORT="${BMS_WEB_HOST_PORT:-18080}"' in runtime_script


def test_frontend_dev_server_owns_vite_default_port_with_hmr_enabled() -> None:
    vite_config = (REPO_ROOT / "platform" / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    assert "strictPort: true" in vite_config
    assert "hmr: false" not in vite_config
    assert "127.0.0.1:5173" in vite_config


def test_one_command_ui_surface_smoke_checker_exists() -> None:
    smoke_script = REPO_ROOT / "scripts" / "smoke_ui_surfaces.py"

    assert smoke_script.exists()
    text = smoke_script.read_text(encoding="utf-8")
    assert "http://127.0.0.1:5173/@vite/client" in text
    assert "http://127.0.0.1:18080/bms/" in text
    assert "platform/desktop-electron" in text


def test_api_dockerfile_uses_prebuilt_venv_at_runtime() -> None:
    dockerfile = (REPO_ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")

    assert "RUN uv sync --frozen --no-dev" in dockerfile
    assert 'CMD ["/app/platform/api/.venv/bin/uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]' in dockerfile
    assert 'CMD ["uv", "run", "uvicorn"' not in dockerfile


def test_httpx_is_a_runtime_dependency_for_container_api_startup() -> None:
    pyproject = (REPO_ROOT / "platform" / "api" / "pyproject.toml").read_text(encoding="utf-8")

    assert '    "httpx>=0.27.0",' in pyproject
    assert 'dev = [\n    "pytest>=8.0.0",\n    "pytest-asyncio>=0.23.0",\n]' in pyproject


def test_workflow_adapter_script_runs_host_native_adapter_without_recursive_routing() -> None:
    adapter_script = (REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh").read_text(encoding="utf-8")

    assert "unset BMS_WORKFLOW_ADAPTER_URL" in adapter_script
    assert "export BMS_CORE_RUNTIME_MODE=0" in adapter_script
    assert 'BMS_WORKFLOW_ADAPTER_BIND_HOST="${BMS_WORKFLOW_ADAPTER_BIND_HOST:-127.0.0.1}"' in adapter_script
    assert 'uv run uvicorn workflow_adapter_app:app --port 8001 --host "$BMS_WORKFLOW_ADAPTER_BIND_HOST"' in adapter_script
