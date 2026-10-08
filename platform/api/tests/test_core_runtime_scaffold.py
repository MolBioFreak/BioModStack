from __future__ import annotations

from pathlib import Path
import importlib.util

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
        "docker/web.Dockerfile",
        "docker/web/nginx.conf",
        "scripts/run_biomodstack_core_runtime.sh",
        "scripts/run_biomodstack_workflow_adapter.sh",
    ]

    missing = [path for path in expected_paths if not (REPO_ROOT / path).exists()]
    assert missing == []


def test_api_runtime_image_keeps_plannotate_runtime_available() -> None:
    dockerfile = (REPO_ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")

    assert "FROM api-base AS api-runtime" in dockerfile
    assert "BMS_MICROMAMBA_BIN=/usr/local/bin/micromamba" in dockerfile
    assert "BMS_MICROMAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX}" in dockerfile
    assert "BMS_PLANNOTATE_ENV=plannotate" in dockerfile
    assert "https://micro.mamba.pm/api/micromamba/linux-64/latest" in dockerfile
    assert "micromamba --root-prefix \"${MAMBA_ROOT_PREFIX}\" create" in dockerfile
    assert "-c conda-forge -c bioconda plannotate" in dockerfile
    assert '"pandas<3"' in dockerfile
    assert '"setuptools<81"' in dockerfile
    assert "streamlit.web.cli" in dockerfile
    assert ".any(axis=1) #only the rows that are in the columns of hit" in dockerfile
    assert "plannotate setupdb" in dockerfile


def test_compose_core_runtime_contract() -> None:
    compose = yaml.safe_load((REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"bms-api", "bms-db", "bms-cpu-power", "bms-host-agent", "bms-stats-tools", "bms-web"}

    api = compose["services"]["bms-api"]
    assert api["build"]["dockerfile"] == "docker/api.Dockerfile"
    assert api["build"]["target"] == "api-runtime"
    assert api["container_name"] == "biomodstack-api"
    assert api["network_mode"] == "host"
    assert "group_add" not in api
    assert "ports" not in api
    assert "extra_hosts" not in api
    assert api["environment"]["BMS_HOME"] == "/app"
    assert api["environment"]["BMS_CORE_RUNTIME_MODE"] == "${BMS_CORE_RUNTIME_MODE:-1}"
    assert api["environment"]["BMS_WORKFLOW_ADAPTER_URL"] == "${BMS_WORKFLOW_ADAPTER_URL:-http://127.0.0.1:8001}"
    assert api["environment"]["BMS_HOST_AGENT_URL"] == "${BMS_HOST_AGENT_URL:-http://127.0.0.1:8798}"
    assert api["environment"]["BMS_HOST_AGENT_TIMEOUT_SECONDS"] == "${BMS_HOST_AGENT_TIMEOUT_SECONDS:-2.0}"
    assert api["environment"]["BMS_CPU_POWER_COLLECTOR_URL"] == "${BMS_CPU_POWER_COLLECTOR_URL:-http://127.0.0.1:8797/power}"
    assert api["environment"]["BMS_ANALYTICAL_DATABASE_URL"] == "${BMS_ANALYTICAL_DATABASE_URL:-postgresql+asyncpg://bms_assay:${BMS_ANALYTICAL_DB_PASSWORD:?set BMS_ANALYTICAL_DB_PASSWORD in local env}@127.0.0.1:${BMS_ANALYTICAL_DB_PORT:-55432}/bms_analytical_data}"
    assert api["environment"]["BMS_ANALYTICAL_INIT_ON_STARTUP"] == "${BMS_ANALYTICAL_INIT_ON_STARTUP:-1}"
    assert api["environment"]["BMS_DB_DISPLAY_NAME"] == "${BMS_DB_DISPLAY_NAME:-BMS DB service}"
    assert api["environment"]["BMS_DB_COMPOSE_SERVICES"] == "${BMS_DB_COMPOSE_SERVICES:-bms-db}"
    assert api["environment"]["BMS_DB_CONTAINER_NAMES"] == "${BMS_DB_CONTAINER_NAMES:-biomodstack-db}"
    assert api["environment"]["BMS_DB_LEGACY_COMPOSE_SERVICES"] == "${BMS_DB_LEGACY_COMPOSE_SERVICES:-bms-analytical-postgres}"
    assert api["environment"]["BMS_DB_LEGACY_CONTAINER_NAMES"] == "${BMS_DB_LEGACY_CONTAINER_NAMES:-biomodstack-analytical-postgres}"
    assert api["environment"]["BMS_STATS_TOOLS_EXTERNALIZED"] == "${BMS_STATS_TOOLS_EXTERNALIZED:-1}"
    assert api["environment"]["BMS_STATS_TOOLS_COMPOSE_FILE"] == "/app/compose.core-runtime.yml"
    assert api["environment"]["BMS_DOCKER_COMPOSE_PROJECT"] == "${BMS_DOCKER_COMPOSE_PROJECT:-biomodstack-core-runtime}"
    assert "BMS_DOCKER_GID" not in api["environment"]
    assert api["environment"]["CORS_ORIGINS"] == "${CORS_ORIGINS:-http://127.0.0.1,http://127.0.0.1:5173,http://127.0.0.1:18080,http://localhost,https://localhost,http://localhost:5173,http://localhost:18080,https://localhost:5173,https://127.0.0.1}"
    assert api["environment"]["BMS_WEIGHTS"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/weights"
    assert api["environment"]["BMS_COLABFOLD_DB"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/colabfold_db"
    assert api["environment"]["BMS_MSA_CACHE"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/msa_cache"
    assert api["environment"]["BMS_SABDAB_CACHE"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/sabdab_cache"
    assert api["environment"]["BMS_WORK"] == "${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}/work"
    assert "BIOXP_SERVER_URL" in api["environment"]
    assert not any(
        volume.get("source") == "/var/run/docker.sock" or volume.get("target") == "/var/run/docker.sock"
        for volume in api.get("volumes", [])
    )

    db_service = compose["services"]["bms-db"]
    assert db_service["image"] == "postgres:16-alpine"
    assert db_service["container_name"] == "biomodstack-db"
    assert db_service["ports"] == ["127.0.0.1:${BMS_ANALYTICAL_DB_PORT:-55432}:5432"]
    assert db_service["environment"]["POSTGRES_DB"] == "bms_analytical_data"
    assert db_service["environment"]["POSTGRES_USER"] == "bms_assay"
    assert db_service["environment"]["POSTGRES_PASSWORD"] == "${BMS_ANALYTICAL_DB_PASSWORD:?set BMS_ANALYTICAL_DB_PASSWORD in local env}"
    assert db_service["volumes"] == ["bms_db_service_data:/var/lib/postgresql/data"]
    assert db_service["networks"]["default"]["aliases"] == ["bms-analytical-postgres"]
    assert db_service["labels"]["org.biomodstack.service_id"] == "bms-db-service"
    assert db_service["labels"]["org.biomodstack.component"] == "db-service"
    assert db_service["labels"]["org.biomodstack.display_name"] == "BMS DB service"
    assert db_service["labels"]["org.biomodstack.optional_at_boot"] == "true"
    assert "healthcheck" in db_service
    assert "bms-db" not in api.get("depends_on", {})

    cpu_power = compose["services"]["bms-cpu-power"]
    assert cpu_power["build"]["dockerfile"] == "docker/api.Dockerfile"
    assert cpu_power["build"]["target"] == "api-runtime"
    assert cpu_power["container_name"] == "biomodstack-cpu-power"
    assert cpu_power["network_mode"] == "host"
    assert cpu_power["user"] == "0:0"
    assert cpu_power["environment"]["BMS_POWER_CAP_ROOT"] == "/host_sys/class/powercap"
    assert cpu_power["environment"]["BMS_CPU_POWER_BIND_HOST"] == "127.0.0.1"
    assert cpu_power["environment"]["BMS_CPU_POWER_PORT"] == "${BMS_CPU_POWER_PORT:-8797}"
    assert cpu_power["command"] == ["python", "/app/platform/api/tools/cpu_power_collector.py"]
    assert cpu_power["volumes"][0]["source"] == "/sys"
    assert cpu_power["volumes"][0]["read_only"] is True

    stats_tools = compose["services"]["bms-stats-tools"]
    assert stats_tools["profiles"] == ["stats-tools"]
    assert stats_tools["build"]["dockerfile"] == "docker/api.Dockerfile"
    assert stats_tools["build"]["target"] == "stats-tools-runtime"
    assert stats_tools["container_name"] == "biomodstack-stats-tools"
    assert stats_tools["environment"]["BMS_STATS_TOOLS_EXTERNALIZED"] == "1"
    assert "bms-db" not in stats_tools.get("depends_on", {})
    assert stats_tools["environment"]["BMS_ANALYTICAL_DATABASE_URL"] == "${BMS_ANALYTICAL_DATABASE_URL:-postgresql+asyncpg://bms_assay:${BMS_ANALYTICAL_DB_PASSWORD:?set BMS_ANALYTICAL_DB_PASSWORD in local env}@bms-db:5432/bms_analytical_data}"
    assert stats_tools["labels"]["org.biomodstack.service_id"] == "bms-stats-tools"
    assert stats_tools["labels"]["org.biomodstack.component"] == "stats-tools"
    assert stats_tools["labels"]["org.biomodstack.optional_at_boot"] == "true"
    assert stats_tools["command"] == ["/app/platform/api/.venv/bin/python", "-c", "import time; print('bms-stats-tools ready', flush=True); time.sleep(10**9)"]
    assert stats_tools["healthcheck"]["test"] == ["CMD", "/app/platform/api/.venv/bin/python", "-c", "import importlib.util; assert importlib.util.find_spec('statsmodels') is not None"]

    web = compose["services"]["bms-web"]
    assert web["build"]["dockerfile"] == "docker/web.Dockerfile"
    assert web["container_name"] == "biomodstack-web"
    assert web["network_mode"] == "host"
    assert "ports" not in web
    assert web["depends_on"]["bms-api"]["condition"] == "service_healthy"
    assert web["environment"]["BMS_WEB_HOST_PORT"] == "${BMS_WEB_HOST_PORT:-18080}"
    assert web["healthcheck"]["test"] == ["CMD-SHELL", 'wget -qO- "http://127.0.0.1:${BMS_WEB_HOST_PORT:-18080}/bms/"']
    assert "bms_db_service_data" in compose["volumes"]
    assert compose["volumes"]["bms_db_service_data"]["name"] == "${BMS_DB_VOLUME_NAME:-biomodstack-core-runtime_bms_analytical_postgres_data}"


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

    for required in [".git", ".venv", ".env", ".env.*", "!.env.core-runtime.example", "platform/frontend/node_modules", "work", "bms_results", "analysis_cache", "*.db"]:
        assert required in dockerignore


def test_vite_config_uses_reproducible_stable_pdbe_alias_and_browser_safe_buffer_resolution() -> None:
    vite_config = (REPO_ROOT / "platform" / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    frontend_package = (REPO_ROOT / "platform" / "frontend" / "package.json").read_text(encoding="utf-8")

    assert "require.resolve('pdbe-molstar-stable/package.json')" in vite_config
    assert "node_modules/.ignored/pdbe-molstar" not in vite_config
    assert '"safe-buffer":' in frontend_package
    assert "safe-buffer" in vite_config
    assert "node_modules/safe-buffer/index.js" in vite_config


def test_vite_config_uses_uid_scoped_cache_dir_outside_repo_node_modules() -> None:
    vite_config = (REPO_ROOT / "platform" / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    assert "function resolveViteCacheDir" in vite_config
    assert "cacheDir: resolveViteCacheDir()" in vite_config
    assert "BMS_VITE_CACHE_DIR" in vite_config
    assert "process.getuid" in vite_config
    assert "os.tmpdir()" in vite_config
    assert "node_modules/.vite" not in vite_config


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
        "BMS_WORK=/var/lib/biomodstack/work",
        "BMS_WEB_HOST_PORT=18080",
        "CORS_ORIGINS=http://127.0.0.1,http://127.0.0.1:5173,http://127.0.0.1:18080,http://localhost,https://localhost,http://localhost:5173,http://localhost:18080,https://localhost:5173,https://127.0.0.1",
        "BMS_CORE_RUNTIME_MODE=1",
        "BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:8001",
        "BMS_ANALYTICAL_DB_PORT=55432",
        "# Set this only in your local runtime env; do not commit real values.",
        "BMS_ANALYTICAL_DB_PASSWORD=",
        "BMS_ANALYTICAL_DATABASE_URL=postgresql+asyncpg://bms_assay:${BMS_ANALYTICAL_DB_PASSWORD}@127.0.0.1:${BMS_ANALYTICAL_DB_PORT}/bms_analytical_data",
        "BMS_ANALYTICAL_INIT_ON_STARTUP=1",
        "BIOXP_SERVER_URL=",
        "BMS_DOCKER_COMPOSE_PROJECT=biomodstack-core-runtime",

        "BMS_STATS_TOOLS_EXTERNALIZED=1",
        "BMS_DB_SERVICE_ID=bms-db-service",
        "BMS_DB_DISPLAY_NAME=\"BMS DB service\"",
        "BMS_DB_COMPOSE_SERVICES=bms-db",
        "BMS_DB_CONTAINER_NAMES=biomodstack-db",
        "BMS_DB_VOLUME_NAME=biomodstack-core-runtime_bms_analytical_postgres_data",
        "BMS_DB_LEGACY_COMPOSE_SERVICES=bms-analytical-postgres",
        "BMS_DB_LEGACY_CONTAINER_NAMES=biomodstack-analytical-postgres",
        "BMS_CORE_DB_NAME=bms_core_runtime",
        "BMS_ANALYTICAL_DB_NAME=bms_analytical_data",
        "BMS_HOST_AGENT_URL=http://127.0.0.1:8798",
        "BMS_HOST_AGENT_TIMEOUT_SECONDS=2.0",
    ]:
        assert required in env_example
    assert "BMS_API_HOST_PORT" not in env_example
    assert "BMS_DOCKER_GID" not in env_example


def test_core_runtime_script_loads_repo_local_env_overrides() -> None:
    runtime_script = (REPO_ROOT / "scripts" / "run_biomodstack_core_runtime.sh").read_text(encoding="utf-8")

    assert ".env.core-runtime.local" in runtime_script
    assert "BMS_CORE_RUNTIME_ENV_FILE" in runtime_script
    assert "--env-file" in runtime_script
    assert 'BMS_WEB_HOST_PORT="${BMS_WEB_HOST_PORT:-18080}"' in runtime_script


def test_stats_tools_cli_uses_core_runtime_project_env_and_python_service() -> None:
    cli = (REPO_ROOT / "scripts" / "bms").read_text(encoding="utf-8")

    assert "BMS_DOCKER_COMPOSE_PROJECT" in cli
    assert "biomodstack-core-runtime" in cli
    assert "BMS_DOCKER_COMPOSE_ENV_FILE" in cli
    assert "BMS_STATS_TOOLS_EXTERNALIZED=\"${BMS_STATS_TOOLS_EXTERNALIZED:-1}\"" in cli
    assert "status|health|logs|start|stop|restart" in cli
    assert "-m services.stats_tools" in cli


def test_db_service_cli_uses_core_runtime_project_env_and_python_service_with_guarded_stop() -> None:
    cli = (REPO_ROOT / "scripts" / "bms").read_text(encoding="utf-8")

    assert "db-service" in cli
    assert "BMS_DB_SERVICE_ID=\"${BMS_DB_SERVICE_ID:-bms-db-service}\"" in cli
    assert "BMS_DB_CONTAINER_NAMES=\"${BMS_DB_CONTAINER_NAMES:-biomodstack-db}\"" in cli
    assert "BMS_DB_LEGACY_CONTAINER_NAMES=\"${BMS_DB_LEGACY_CONTAINER_NAMES:-biomodstack-analytical-postgres}\"" in cli
    assert "status|health|logs|start|restart|stop" in cli
    assert "--i-know-this-disables-db-backed-features" in cli
    assert "-m services.db_service" in cli


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

    assert "FROM python:3.10-slim-bookworm AS api-base" in dockerfile
    assert "FROM api-base AS api-runtime" in dockerfile
    assert "FROM api-base AS stats-tools-runtime" in dockerfile
    assert dockerfile.index("FROM api-base AS api-runtime") < dockerfile.index("FROM api-base AS stats-tools-runtime")
    assert "RUN uv sync --frozen --no-dev" in dockerfile
    assert "COPY --chown=biomodstack:biomodstack . /app" in dockerfile
    assert "docker.io" in dockerfile
    assert "docker-compose" in dockerfile
    assert "ARG BMS_R_INSTALL_NCPUS=1" in dockerfile
    assert "ENV BMS_R_INSTALL_NCPUS=${BMS_R_INSTALL_NCPUS}" in dockerfile
    assert 'CMD ["/app/platform/api/.venv/bin/uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]' in dockerfile
    assert 'CMD ["uv", "run", "uvicorn"' not in dockerfile


def test_api_runtime_build_target_does_not_install_r_stats_stack() -> None:
    dockerfile = (REPO_ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    api_base_text, stats_and_final_text = dockerfile.split("FROM api-base AS stats-tools-runtime", 1)

    assert "r-base" not in api_base_text
    assert "r-cran-tidyverse" not in api_base_text
    assert "Rscript /app/docker/install_assay_r_packages.R" not in api_base_text
    assert "RUN Rscript /app/docker/install_assay_r_packages.R" in stats_and_final_text
    assert "r-base" in stats_and_final_text


def test_assay_r_package_installer_caps_parallel_compilation_by_default() -> None:
    installer = (REPO_ROOT / "docker" / "install_assay_r_packages.R").read_text(encoding="utf-8")

    assert 'Sys.getenv("BMS_R_INSTALL_NCPUS", "1")' in installer
    assert "Ncpus = r_install_ncpus()" in installer
    assert "parallel::detectCores() - 1" not in installer


def test_httpx_is_a_runtime_dependency_for_container_api_startup() -> None:
    pyproject = (REPO_ROOT / "platform" / "api" / "pyproject.toml").read_text(encoding="utf-8")

    assert '    "httpx>=0.27.0",' in pyproject
    assert 'dev = [\n    "pytest>=8.0.0",\n    "pytest-asyncio>=0.23.0",\n]' in pyproject


def test_core_runtime_image_proof_script_reports_safe_api_runtime_contract() -> None:
    module = _load_api_image_proof_module()

    assessment = module.assess_repo_contract(REPO_ROOT)

    assert assessment["ok"] is True
    assert assessment["compose_project"] == "biomodstack-core-runtime"
    assert assessment["api_service"]["build_target"] == "api-runtime"
    assert assessment["stats_tools_service"]["build_target"] == "stats-tools-runtime"
    assert assessment["api_runtime_stage_before_stats_tools_stage"] is True
    assert assessment["api_runtime_prefix_has_r_stack"] is False
    assert assessment["dockerignore_excludes_env_files"] is True
    assert assessment["api_runtime_forbidden_markers"] == []


def test_core_runtime_image_proof_script_redacts_credentials_from_logs_and_plans() -> None:
    module = _load_api_image_proof_module()

    redacted = module.redact_text(
        "postgresql+asyncpg://bms_assay:super-secret@127.0.0.1:55432/bms_analytical_data\n"
        "POSTGRES_PASSWORD=super-secret\n"
        "BMS_ANALYTICAL_DATABASE_URL=postgresql://user:pw@example/db\n"
    )

    assert "super-secret" not in redacted
    assert "user:pw@" not in redacted
    assert "POSTGRES_PASSWORD=[REDACTED]" in redacted
    assert "BMS_ANALYTICAL_DATABASE_URL=[REDACTED]" in redacted
    assert "bms_assay:***@127.0.0.1" in redacted


def test_core_runtime_image_proof_cli_is_exposed_from_bms_operator_script() -> None:
    cli = (REPO_ROOT / "scripts" / "bms").read_text(encoding="utf-8")

    assert "bms api-image preflight" in cli
    assert "bms api-image plan" in cli
    assert "scripts/bms_api_image_proof.py" in cli
    assert "preflight|plan" in cli


def test_core_runtime_image_proof_plan_uses_explicit_project_no_stats_rebuild_and_force_recreate() -> None:
    module = _load_api_image_proof_module()

    plan = module.render_recreate_plan(REPO_ROOT)

    assert "docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml build bms-api" in plan
    assert "docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml up -d --no-deps --force-recreate bms-api" in plan
    assert "bms-stats-tools" not in plan
    assert "--build" not in plan
    assert "BMS DB service" in plan


def test_workflow_adapter_script_runs_host_native_adapter_without_recursive_routing() -> None:
    adapter_script = (REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh").read_text(encoding="utf-8")

    assert "unset BMS_WORKFLOW_ADAPTER_URL" in adapter_script
    assert "export BMS_CORE_RUNTIME_MODE=0" in adapter_script
    assert 'export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"' in adapter_script
    assert 'BMS_WORKFLOW_ADAPTER_BIND_HOST="${BMS_WORKFLOW_ADAPTER_BIND_HOST:-127.0.0.1}"' in adapter_script
    assert 'uv run uvicorn workflow_adapter_app:app --port 8001 --host "$BMS_WORKFLOW_ADAPTER_BIND_HOST"' in adapter_script


def test_workflow_adapter_script_loads_install_profile_runtime_paths_after_legacy_env() -> None:
    adapter_script = (REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh").read_text(encoding="utf-8")

    assert "load_env_file_overrides" in adapter_script
    assert 'PROFILE_CORE_RUNTIME_ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/biomodstack/core-runtime.env"' in adapter_script
    assert 'load_env_file_overrides "$CORE_RUNTIME_ENV_FILE"' in adapter_script
    legacy_source_index = adapter_script.index('source "$HOME/.biomodstack/env.sh"')
    profile_source_index = adapter_script.index('load_env_file_overrides "$CORE_RUNTIME_ENV_FILE"')
    assert legacy_source_index < profile_source_index


def test_workflow_adapter_script_uses_durable_nextflow_home_not_tmp_cache() -> None:
    adapter_script = (REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh").read_text(encoding="utf-8")

    assert 'BMS_NEXTFLOW_HOME="${BMS_NEXTFLOW_HOME:-${BMS_DATA:-/mnt/BioModStack}/nextflow}"' in adapter_script
    assert 'export NXF_HOME="${NXF_HOME:-$BMS_NEXTFLOW_HOME}"' in adapter_script
    assert 'mkdir -p "$NXF_HOME"' in adapter_script
    assert "/tmp/nxf-home" not in adapter_script
