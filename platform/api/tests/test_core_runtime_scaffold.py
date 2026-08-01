from __future__ import annotations

from pathlib import Path
import importlib.util
import tomllib

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_api_image_proof_module():
    script_path = REPO_ROOT / "scripts" / "bms_api_image_proof.py"
    spec = importlib.util.spec_from_file_location("bms_api_image_proof", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_core_runtime_scaffold_files_exist() -> None:
    expected_paths = [
        "compose.core-runtime.yml",
        ".env.core-runtime.example",
        ".dockerignore",
        "docker/api.Dockerfile",
        "docker/plannotate-conda-linux-64.lock",
        "docker/web.Dockerfile",
        "docker/web/nginx.conf",
        "scripts/run_biomodstack_core_runtime.sh",
        "scripts/run_biomodstack_workflow_adapter.sh",
    ]

    missing = [path for path in expected_paths if not (REPO_ROOT / path).exists()]
    assert missing == []


def test_api_runtime_image_keeps_plannotate_runtime_available() -> None:
    dockerfile = (REPO_ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")

    assert (
        "FROM python:3.10-slim-bookworm@sha256:"
        "9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015 AS api-base"
        in dockerfile
    )
    assert "FROM api-base AS api-runtime-prepared" in dockerfile
    assert "FROM scratch AS api-runtime" in dockerfile
    assert "BMS_MICROMAMBA_BIN=/usr/local/bin/micromamba" in dockerfile
    assert "BMS_MICROMAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX}" in dockerfile
    assert "BMS_PLANNOTATE_ENV=plannotate" in dockerfile
    assert "BMS_PLANNOTATE_VERSION=2.0.0" in dockerfile
    assert "BMS_PLANNOTATE_STREAMLIT_VERSION=1.59.2" in dockerfile
    assert "micromamba-releases/releases/download/2.5.0-2/micromamba-linux-64" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "micromamba --root-prefix \"${MAMBA_ROOT_PREFIX}\" create" in dockerfile
    assert "--file /app/docker/plannotate-conda-linux-64.lock" in dockerfile
    assert "plannotate setupdb" in dockerfile
    assert "plannotate databases" in dockerfile
    assert "import plannotate.streamlit_app" in dockerfile
    assert "streamlit.web.cli" not in dockerfile
    assert ".any(axis=1) #only the rows that are in the columns of hit" not in dockerfile

    lock_lines = (REPO_ROOT / "docker" / "plannotate-conda-linux-64.lock").read_text(
        encoding="utf-8"
    ).splitlines()
    assert lock_lines[0] == "@EXPLICIT"
    assert len(lock_lines) == 215
    assert any("python-3.12.13-" in line for line in lock_lines)
    assert any("plannotate-2.0.0-" in line for line in lock_lines)
    assert any("plannotate-2.0.0-pyhdfd78af_0.conda#" in line for line in lock_lines)
    assert any("streamlit-1.59.2-" in line for line in lock_lines)
    assert any("pandas-2.3.3-" in line for line in lock_lines)
    assert any("bokeh-3.9.1-" in line for line in lock_lines)
    for line in lock_lines[1:]:
        url, sha256 = line.rsplit("#", 1)
        assert url.startswith("https://")
        assert len(sha256) == 64
        assert all(character in "0123456789abcdef" for character in sha256)
    assert 'rm -rf "${MAMBA_ROOT_PREFIX}/pkgs"' in dockerfile
    assert "/usr/local/lib/python3.10/site-packages/setuptools*" in dockerfile
    assert "/usr/local/bin/uv /usr/local/bin/uvx" in dockerfile
    assert 'conda-meta/history"' in dockerfile
    assert "os.utime(path, (epoch, epoch), follow_symlinks=False)" in dockerfile


def test_compose_core_runtime_contract() -> None:
    compose = yaml.safe_load(
        (REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8")
    )

    assert set(compose["services"]) == {
        "bms-api",
        "bms-cpu-power",
        "bms-host-agent",
        "bms-web",
    }

    api = compose["services"]["bms-api"]
    assert api["build"]["dockerfile"] == "docker/api.Dockerfile"
    assert api["build"]["target"] == "api-runtime"
    assert api["container_name"] == "biomodstack-api"
    assert api["network_mode"] == "host"
    assert api["group_add"] == ["${BMS_MK1D_RECOVERY_GID:-65534}"]
    assert api["volumes"][-1] == {
        "type": "bind",
        "source": "${BMS_MK1D_RECOVERY_SOCKET_DIR:-/run/biomodstack}",
        "target": "/run/biomodstack",
        "read_only": True,
    }
    assert "ports" not in api
    assert "extra_hosts" not in api
    assert api["environment"]["BMS_HOME"] == "/app"
    assert api["environment"]["BMS_CORE_RUNTIME_MODE"] == "${BMS_CORE_RUNTIME_MODE:-1}"
    assert (
        api["environment"]["BMS_WORKFLOW_ADAPTER_URL"]
        == "${BMS_WORKFLOW_ADAPTER_URL:-http://127.0.0.1:8001}"
    )
    assert (
        api["environment"]["BMS_HOST_AGENT_URL"]
        == "${BMS_HOST_AGENT_URL:-http://127.0.0.1:8798}"
    )
    assert (
        api["environment"]["BMS_HOST_AGENT_TIMEOUT_SECONDS"]
        == "${BMS_HOST_AGENT_TIMEOUT_SECONDS:-2.0}"
    )
    assert (
        api["environment"]["BMS_CPU_POWER_COLLECTOR_URL"]
        == "${BMS_CPU_POWER_COLLECTOR_URL:-http://127.0.0.1:8797/power}"
    )
    assert (
        api["environment"]["BMS_DOCKER_COMPOSE_PROJECT"]
        == "${BMS_DOCKER_COMPOSE_PROJECT:-biomodstack-core-runtime}"
    )
    assert "BMS_DOCKER_GID" not in api["environment"]
    assert "BMS_FRONTEND_HEALTH_URL" not in api["environment"]
    assert api["environment"]["CORS_ORIGINS"] == "${CORS_ORIGINS:-http://127.0.0.1,http://127.0.0.1:5173,http://127.0.0.1:18080,http://localhost,https://localhost,http://localhost:5173,http://localhost:18080,https://localhost:5173,https://127.0.0.1}"
    assert api["environment"]["BMS_WEIGHTS"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/weights"
    assert api["environment"]["BMS_COLABFOLD_DB"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/colabfold_db"
    assert api["environment"]["BMS_MSA_CACHE"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/msa_cache"
    assert api["environment"]["BMS_SABDAB_CACHE"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/sabdab_cache"
    assert api["environment"]["BMS_WORK"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/work"
    bioxp_environment = api["environment"]
    assert bioxp_environment["BMS_FEATURE_BIOXP"] == "${BMS_FEATURE_BIOXP:-1}"
    assert (
        bioxp_environment["BMS_BIOXP_MUTATIONS_ENABLED"]
        == "${BMS_BIOXP_MUTATIONS_ENABLED:-0}"
    )
    assert (
        bioxp_environment["BMS_BIOXP_ALLOWED_HOSTS"]
        == "${BMS_BIOXP_ALLOWED_HOSTS:-robot}"
    )
    assert bioxp_environment["BMS_BIOXP_ALLOWED_CIDRS"] == "${BMS_BIOXP_ALLOWED_CIDRS:-}"
    for retired in ("BIOXP_SERVER_URL", "BIOXP_LINKAGE_URL", "BIOXP_LINKAGE_STATE_PATH"):
        assert retired not in bioxp_environment
    assert not any(
        volume.get("source") == "/var/run/docker.sock"
        or volume.get("target") == "/var/run/docker.sock"
        for volume in api.get("volumes", [])
    )

    cpu_power = compose["services"]["bms-cpu-power"]
    assert cpu_power["build"]["dockerfile"] == "docker/api.Dockerfile"
    assert cpu_power["build"]["target"] == "api-runtime"
    assert cpu_power["container_name"] == "biomodstack-cpu-power"
    assert cpu_power["network_mode"] == "host"
    assert cpu_power["user"] == "0:0"
    assert cpu_power["environment"]["BMS_POWER_CAP_ROOT"] == "/host_sys/class/powercap"
    assert cpu_power["environment"]["BMS_CPU_POWER_BIND_HOST"] == "127.0.0.1"
    assert (
        cpu_power["environment"]["BMS_CPU_POWER_PORT"] == "${BMS_CPU_POWER_PORT:-8797}"
    )
    assert cpu_power["command"] == [
        "python",
        "/app/platform/api/tools/cpu_power_collector.py",
    ]
    assert cpu_power["volumes"][0]["source"] == "/sys"
    assert cpu_power["volumes"][0]["read_only"] is True

    web = compose["services"]["bms-web"]
    assert web["build"]["dockerfile"] == "docker/web.Dockerfile"
    assert web["container_name"] == "biomodstack-web"
    assert web["network_mode"] == "host"
    assert "ports" not in web
    assert web["depends_on"]["bms-api"]["condition"] == "service_healthy"
    assert web["environment"]["BMS_WEB_HOST_PORT"] == "${BMS_WEB_HOST_PORT:-18080}"
    assert web["healthcheck"]["test"] == [
        "CMD-SHELL",
        'wget -qO- "http://127.0.0.1:${BMS_WEB_HOST_PORT:-18080}/bms/"',
    ]
    assert not compose.get("volumes")



def test_mk1d_reconnect_runtime_group_is_loaded_only_from_validated_root_config() -> None:
    launcher = (REPO_ROOT / "scripts" / "run_biomodstack_core_runtime.sh").read_text(encoding="utf-8")
    example = (REPO_ROOT / ".env.core-runtime.example").read_text(encoding="utf-8")

    assert "load_root_owned_mk1d_recovery_gid" in launcher
    assert "stat -c '%u:%g:%a'" in launcher
    assert "0:0:644" in launcher
    assert "BMS_MK1D_RECOVERY_GID" in launcher
    assert "load_root_owned_mk1d_recovery_gid" in launcher.split("load_env_file_overrides", 1)[-1]
    assert "BMS_MK1D_RECOVERY_GID=" not in example
    assert "BMS_MK1D_RECONNECT_TIMEOUT_SECONDS=100" in example


def test_nginx_contract_preserves_bms_and_api_routes() -> None:
    nginx_conf = (REPO_ROOT / "docker" / "web" / "nginx.conf").read_text(
        encoding="utf-8"
    )

    assert "listen 127.0.0.1:${BMS_WEB_HOST_PORT};" in nginx_conf
    assert "absolute_redirect off;" in nginx_conf
    assert "location = / {" in nginx_conf
    assert "return 302 /bms/;" in nginx_conf
    assert "location /bms/ {" in nginx_conf
    assert "try_files $uri $uri/ /bms/index.html;" in nginx_conf
    assert "location /api/ {" in nginx_conf
    assert "proxy_pass http://127.0.0.1:8000;" in nginx_conf


def test_mk1d_reconnect_is_a_local_bms_host_route_and_tailnet_cannot_forward_it() -> None:
    api_compose = yaml.safe_load((REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8"))
    tailnet_compose = yaml.safe_load((REPO_ROOT / "compose.tailnet-control.yml").read_text(encoding="utf-8"))
    web_nginx = (REPO_ROOT / "docker" / "web" / "nginx.conf").read_text(encoding="utf-8")
    tailnet_nginx = (REPO_ROOT / "docker" / "tailnet-production-proxy.conf").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / ".env.core-runtime.example").read_text(encoding="utf-8")

    api_environment = api_compose["services"]["bms-api"]["environment"]
    web_environment = api_compose["services"]["bms-web"]["environment"]
    assert api_environment["BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET"] == "${BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET:-}"
    assert web_environment["BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET"] == "${BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET:-}"
    assert "BMS_MK1D_RECONNECT_TRUSTED_PROXY_HOSTS" not in api_environment
    assert "BMS_MK1D_RECONNECT_ALLOWED_TAILSCALE_USERS" not in api_environment
    assert "BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET=" in env_example
    assert "BMS_MK1D_RECONNECT_ALLOWED_TAILSCALE_USERS" not in env_example

    reconnect_location = web_nginx.split("location = /api/ont/devices/reconnect {", 1)[1].split("\n    }", 1)[0]
    assert "allow 127.0.0.1;" in reconnect_location
    assert "allow ::1;" in reconnect_location
    assert "deny all;" in reconnect_location
    assert 'proxy_set_header X-BMS-MK1D-Reconnect-Proxy-Secret "${BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET}";' in reconnect_location
    assert 'proxy_set_header Tailscale-User-Login "";' in reconnect_location
    assert "Tailscale-User-Login $http_" not in reconnect_location
    assert "X-BMS-CM-Proxy-Secret" not in reconnect_location

    tailnet_location = tailnet_nginx.split("location = /api/ont/devices/reconnect {", 1)[1].split("\n    }", 1)[0]
    assert "return 403;" in tailnet_location
    assert "proxy_pass" not in tailnet_location
    assert "BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET" not in tailnet_compose["services"]["tailnet-production-proxy"].get("environment", {})
    assert tailnet_compose["services"]["tailnet-production-proxy"]["volumes"][0].endswith(":/etc/nginx/conf.d/default.conf:ro")


def test_dockerignore_keeps_local_runtime_state_out_of_images() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

    for required in [
        ".git",
        ".venv",
        ".env",
        ".env.*",
        "!.env.core-runtime.example",
        "platform/frontend/node_modules",
        "work",
        "bms_results",
        "analysis_cache",
        "*.db",
    ]:
        assert required in dockerignore


def test_vite_config_uses_direct_molstar_and_browser_safe_buffer_resolution() -> None:
    vite_config = (REPO_ROOT / "platform" / "frontend" / "vite.config.ts").read_text(
        encoding="utf-8"
    )
    frontend_package = (REPO_ROOT / "platform" / "frontend" / "package.json").read_text(
        encoding="utf-8"
    )

    assert "pdbe-molstar" not in vite_config.casefold()
    assert "normalized.includes('/node_modules/molstar')" in vite_config
    assert '"molstar":' in frontend_package
    assert '"safe-buffer":' in frontend_package
    assert "safe-buffer" in vite_config
    assert "node_modules/safe-buffer/index.js" in vite_config


def test_vite_config_uses_uid_scoped_cache_dir_outside_repo_node_modules() -> None:
    vite_config = (REPO_ROOT / "platform" / "frontend" / "vite.config.ts").read_text(
        encoding="utf-8"
    )

    assert "function resolveViteCacheDir" in vite_config
    assert "cacheDir: resolveViteCacheDir()" in vite_config
    assert "BMS_VITE_CACHE_DIR" in vite_config
    assert "process.getuid" in vite_config
    assert "os.tmpdir()" in vite_config
    assert "node_modules/.vite" not in vite_config


def test_frontend_router_uses_vite_base_url_for_subpath_deployments() -> None:
    main_tsx = (REPO_ROOT / "platform" / "frontend" / "src" / "main.tsx").read_text(
        encoding="utf-8"
    )

    assert "resolveRouterBasenameForLocation(" in main_tsx
    assert "{ envBaseUrl: import.meta.env.BASE_URL }" in main_tsx
    assert "basename={routerBasename}" in main_tsx
    assert (
        "isAppPath(window.location.pathname, '/designer', routerBasename)" in main_tsx
    )


def test_core_runtime_env_example_documents_transition_knobs() -> None:
    env_example = (REPO_ROOT / ".env.core-runtime.example").read_text(encoding="utf-8")

    for required in [
        "BMS_STATE_DIR=",
        "BMS_CONTAINER_STATE_PATH=",
        "BMS_WORK=/var/lib/biomodstack/work",
        "BMS_WEB_HOST_PORT=18080",
        "CORS_ORIGINS=http://127.0.0.1,http://127.0.0.1:5173,http://127.0.0.1:18080,http://localhost,https://localhost,http://localhost:5173,http://localhost:18080,https://localhost:5173,https://127.0.0.1",
        "BMS_CORE_RUNTIME_MODE=1",
        "BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:8001",
        "BMS_FEATURE_BIOXP=1",
        "BMS_BIOXP_MUTATIONS_ENABLED=0",
        "BMS_BIOXP_ALLOWED_HOSTS=robot",
        "BMS_BIOXP_ALLOWED_CIDRS=",
        "BMS_DOCKER_COMPOSE_PROJECT=biomodstack-core-runtime",
        "BMS_HOST_AGENT_URL=http://127.0.0.1:8798",
        "BMS_HOST_AGENT_TIMEOUT_SECONDS=2.0",
    ]:
        assert required in env_example
    assert "BMS_API_HOST_PORT" not in env_example
    assert "BMS_DOCKER_GID" not in env_example
    for retired in (
        "BMS_ANALY" + "TICAL_",
        "BMS_FEATURE_STA" + "TS_TOOLS",
        "BMS_FEATURE_AS" + "SAY_DB",
    ):
        assert retired not in env_example


def test_core_runtime_script_loads_repo_local_env_overrides() -> None:
    runtime_script = (
        REPO_ROOT / "scripts" / "run_biomodstack_core_runtime.sh"
    ).read_text(encoding="utf-8")

    assert ".env.core-runtime.local" in runtime_script
    assert "BMS_CORE_RUNTIME_ENV_FILE" in runtime_script
    assert "--env-file" in runtime_script
    assert 'BMS_WEB_HOST_PORT="${BMS_WEB_HOST_PORT:-18080}"' in runtime_script


def test_frontend_dev_server_owns_vite_default_port_with_hmr_enabled() -> None:
    vite_config = (REPO_ROOT / "platform" / "frontend" / "vite.config.ts").read_text(
        encoding="utf-8"
    )

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

    assert (
        "FROM python:3.10-slim-bookworm@sha256:"
        "9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015 AS api-base"
        in dockerfile
    )
    assert dockerfile.startswith("ARG SOURCE_DATE_EPOCH=1\n")
    assert "FROM api-base AS api-runtime-prepared" in dockerfile
    assert "FROM scratch AS api-runtime" in dockerfile
    assert "UV_COMPILE_BYTECODE=0" in dockerfile
    assert "UV_CACHE_DIR=/tmp/uv-cache" in dockerfile
    assert 'Path("/usr/local/lib/python3.10/site-packages/uv")' in dockerfile
    assert 'root.rglob("__pycache__")' in dockerfile
    assert "&& uv sync --frozen --no-dev" in dockerfile
    assert 'rm -rf "${UV_CACHE_DIR}" /home/biomodstack/.cache/uv' in dockerfile
    assert "/var/log/dpkg.log" in dockerfile
    assert "RUN --mount=type=bind,source=.,target=/src,readonly" in dockerfile
    assert "mkdir -p /app/platform/api /var/lib/biomodstack" in dockerfile
    assert "COPY --chown=biomodstack:biomodstack . /app" not in dockerfile
    assert "COPY --from=api-runtime-prepared / /" in dockerfile
    assert "USER 1000:1000" in dockerfile
    assert "os.utime(path, (epoch, epoch), follow_symlinks=False)" in dockerfile
    assert "docker.io" in dockerfile
    assert "docker-compose" in dockerfile
    assert "/app/platform/api/.venv/bin/python run_migrations.py" in dockerfile
    assert "exec /app/platform/api/.venv/bin/uvicorn main:app" in dockerfile
    assert "--host 127.0.0.1 --port 8000" in dockerfile
    assert 'CMD ["uv", "run", "uvicorn"' not in dockerfile


def test_hosted_web_html_sets_strict_electron_compatible_csp() -> None:
    nginx = (REPO_ROOT / "docker" / "web" / "nginx.conf").read_text(encoding="utf-8")

    assert "add_header Content-Security-Policy" in nginx
    assert "script-src 'self' 'wasm-unsafe-eval'" in nginx
    assert "script-src 'self' 'unsafe-eval'" not in nginx
    assert "object-src 'none'" in nginx
    assert "frame-ancestors 'none'" in nginx


def test_api_runtime_image_has_no_retired_analytical_build_stage() -> None:
    dockerfile = (REPO_ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    for retired in (
        "sta" + "ts-tools-runtime",
        "r-base",
        "r-cran-tidyverse",
        "install_" + "as" + "say_r_packages.R",
    ):
        assert retired not in dockerfile


def test_httpx_is_a_runtime_dependency_for_container_api_startup() -> None:
    pyproject_path = REPO_ROOT / "platform" / "api" / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    assert "httpx>=0.27.0" in pyproject["project"]["dependencies"]
    assert {
        "pytest>=8.0.0",
        "pytest-asyncio>=0.23.0",
        "pytest-randomly>=3.15.0",
        "pytest-socket>=0.7.0",
    }.issubset(set(pyproject["dependency-groups"]["dev"]))


def test_core_runtime_image_proof_script_reports_safe_api_runtime_contract() -> None:
    module = _load_api_image_proof_module()

    assessment = module.assess_repo_contract(REPO_ROOT)

    assert assessment["ok"] is True
    assert assessment["compose_project"] == "biomodstack-core-runtime"
    assert assessment["api_service"]["build_target"] == "api-runtime"
    assert assessment["api_runtime_stage_present"] is True
    assert assessment["dockerignore_excludes_env_files"] is True
    assert assessment["issues"] == []


def test_core_runtime_image_proof_script_redacts_credentials_from_logs_and_plans() -> (
    None
):
    module = _load_api_image_proof_module()

    redacted = module.redact_text(
        "https://service-user:super-secret@example.invalid/api\n"
        "SERVICE_PASSWORD=another-secret\n"
    )

    assert "super-secret" not in redacted
    assert "another-secret" not in redacted
    assert "https://service-user:***@example.invalid/api" in redacted
    assert "SERVICE_PASSWORD=[REDACTED]" in redacted


def test_core_runtime_image_proof_cli_is_exposed_from_bms_operator_script() -> None:
    cli = (REPO_ROOT / "scripts" / "bms").read_text(encoding="utf-8")

    assert "bms api-image preflight" in cli
    assert "bms api-image plan" in cli
    assert "scripts/bms_api_image_proof.py" in cli
    assert '"preflight"' in cli
    assert '"plan"' in cli


def test_core_runtime_image_proof_plan_uses_explicit_project_no_stats_rebuild_and_force_recreate() -> (
    None
):
    module = _load_api_image_proof_module()

    plan = module.render_recreate_plan(REPO_ROOT)

    assert (
        "docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml build bms-api"
        in plan
    )
    assert (
        "docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml up -d --no-deps --force-recreate bms-api"
        in plan
    )
    assert "bms-sta" + "ts-tools" not in plan
    assert "--build" not in plan
    assert "BMS DB " + "service" not in plan


def test_core_runtime_image_proof_respects_compose_project_override(
    monkeypatch,
) -> None:
    module = _load_api_image_proof_module()

    monkeypatch.setenv("BMS_DOCKER_COMPOSE_PROJECT", "p1-env-loader-proof")
    assessment = module.assess_repo_contract(REPO_ROOT)
    assert assessment["compose_project"] == "p1-env-loader-proof"


def test_workflow_adapter_script_runs_host_native_adapter_without_recursive_routing() -> (
    None
):
    adapter_script = (
        REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh"
    ).read_text(encoding="utf-8")

    assert "unset BMS_WORKFLOW_ADAPTER_URL" in adapter_script
    assert "export BMS_CORE_RUNTIME_MODE=0" in adapter_script
    assert (
        'export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"'
        in adapter_script
    )
    assert (
        'BMS_WORKFLOW_ADAPTER_BIND_HOST="${BMS_WORKFLOW_ADAPTER_BIND_HOST:-127.0.0.1}"'
        in adapter_script
    )
    for systemd_authority_key in (
        "BMS_FEATURE_MOLECULAR_DYNAMICS",
        "BMS_MD_ANALYSIS_ENABLED",
        "BMS_MD_ANALYSIS_CONTAINER",
        "BMS_MD_ANALYSIS_SIF_SHA256",
        "BMS_MD_ANALYSIS_IMPLEMENTATION_SHA256",
    ):
        assert systemd_authority_key in adapter_script.split(
            "SYSTEMD_AUTHORITY_KEYS=(", 1
        )[1].split(")", 1)[0]
    assert (
        'uv run --no-sync uvicorn workflow_adapter_app:app --port 8001 '
        '--host "$BMS_WORKFLOW_ADAPTER_BIND_HOST" --no-proxy-headers --no-access-log'
        in adapter_script
    )


def test_workflow_adapter_script_loads_install_profile_runtime_paths_after_legacy_env() -> (
    None
):
    adapter_script = (
        REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh"
    ).read_text(encoding="utf-8")

    assert "load_env_file_overrides" in adapter_script
    assert (
        'PROFILE_CORE_RUNTIME_ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/biomodstack/core-runtime.env"'
        in adapter_script
    )
    assert 'load_env_file_overrides "$CORE_RUNTIME_ENV_FILE"' in adapter_script
    legacy_source_index = adapter_script.index('source "$HOME/.biomodstack/env.sh"')
    profile_source_index = adapter_script.index(
        'load_env_file_overrides "$CORE_RUNTIME_ENV_FILE"'
    )
    assert legacy_source_index < profile_source_index


def test_workflow_adapter_script_uses_durable_nextflow_home_not_tmp_cache() -> None:
    adapter_script = (
        REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh"
    ).read_text(encoding="utf-8")

    assert (
        'BMS_NEXTFLOW_HOME="${BMS_NEXTFLOW_HOME:-${BMS_DATA:-/mnt/BioModStack}/nextflow}"'
        in adapter_script
    )
    assert 'export NXF_HOME="${NXF_HOME:-$BMS_NEXTFLOW_HOME}"' in adapter_script
    assert 'mkdir -p "$NXF_HOME"' in adapter_script
    assert "/tmp/nxf-home" not in adapter_script
