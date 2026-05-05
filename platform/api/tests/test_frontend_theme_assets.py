from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_SRC = REPO_ROOT / "platform" / "frontend" / "src"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tailwind_theme_exports_legacy_and_current_semantic_aliases() -> None:
    """Older BMS panels and newer panels should not compile into competing themes."""
    css = read(FRONTEND_SRC / "index.css")

    expected_aliases = [
        "--color-surface: var(--bg-primary);",
        "--color-surface-secondary: var(--bg-secondary);",
        "--color-surface-tertiary: var(--bg-tertiary);",
        "--color-bg-primary: var(--bg-primary);",
        "--color-bg-secondary: var(--bg-secondary);",
        "--color-bg-tertiary: var(--bg-tertiary);",
        "--color-border-primary: var(--border-primary);",
        "--color-border-secondary: var(--border-secondary);",
        "--color-content: var(--text-primary);",
        "--color-content-secondary: var(--text-secondary);",
        "--color-content-muted: var(--text-muted);",
        "--color-text-primary: var(--text-primary);",
        "--color-text-secondary: var(--text-secondary);",
        "--color-text-muted: var(--text-muted);",
    ]
    for alias in expected_aliases:
        assert alias in css


def test_stale_vite_app_css_cannot_override_bms_shell_theme() -> None:
    """App.css must stay inert so it cannot reintroduce the old hard-edged starter UI."""
    app_css = read(FRONTEND_SRC / "App.css")
    main_tsx = read(FRONTEND_SRC / "main.tsx")
    app_tsx = read(FRONTEND_SRC / "App.tsx")

    assert "./App.css" not in main_tsx
    assert "./App.css" not in app_tsx
    forbidden_starter_rules = ["max-width: 1280px", ".logo", ".card", ".read-the-docs", "text-align: center"]
    for rule in forbidden_starter_rules:
        assert rule not in app_css


def test_non_fullscreen_igv_modal_keeps_rounded_bms_surface() -> None:
    """Only true fullscreen modes should intentionally use hard square corners."""
    ngs = read(FRONTEND_SRC / "components" / "NGSToolkit.tsx")

    assert "igvIsFullscreen ? 'rounded-none border-0' : 'rounded-2xl'" in ngs
    assert "igvIsFullscreen ? 'rounded-none border-0' : 'rounded-none'" not in ngs


def test_dashboard_cards_use_canonical_bms_style_track() -> None:
    """Dashboard workbench panels should share one radius/surface recipe."""
    primitives = read(FRONTEND_SRC / "components" / "ui" / "bmsStyle.ts")
    quick_viewer = read(FRONTEND_SRC / "components" / "QuickViewer.tsx")
    job_queue = read(FRONTEND_SRC / "components" / "JobQueuePanel.tsx")
    system_resources = read(FRONTEND_SRC / "components" / "dashboard" / "SystemResources.tsx")

    for token in [
        "BMS_PANEL_SURFACE",
        "BMS_PANEL_SURFACE_SOFT",
        "BMS_PANEL_OVERFLOW",
        "BMS_CONTROL_GROUP",
        "BMS_CONTROL",
        "BMS_VIEWER_WELL",
        "BMS_FULLSCREEN_FLUSH",
    ]:
        assert f"export const {token}" in primitives

    assert "BMS_PANEL_SURFACE" in quick_viewer
    assert "BMS_CONTROL_GROUP" in quick_viewer
    assert "BMS_VIEWER_WELL" in quick_viewer
    assert "BMS_PANEL_OVERFLOW" in job_queue
    assert "BMS_PANEL_SURFACE_SOFT" in system_resources

    assert "Global CSS must not force every BMS panel/card/button to square corners" not in primitives


def test_canonical_bms_style_primitives_are_semantic_theme_tracks() -> None:
    """Shared primitives should follow theme variables, not a competing hard-coded slate theme."""
    primitives = read(FRONTEND_SRC / "components" / "ui" / "bmsStyle.ts")

    expected_tracks = [
        "border-border-primary",
        "bg-surface-secondary/70",
        "bg-surface-secondary/55",
        "border-border-secondary",
        "bg-surface-tertiary/80",
        "bg-surface/50",
    ]
    for track in expected_tracks:
        assert track in primitives

    forbidden_raw_neutrals = ["border-slate-", "bg-slate-", "text-slate-"]
    for raw in forbidden_raw_neutrals:
        assert raw not in primitives


def test_square_corner_usage_is_explicit_and_contextual() -> None:
    """Square corners are allowed only for fullscreen/edge-to-edge surfaces, never as a global reset."""
    css = read(FRONTEND_SRC / "index.css")
    assert "border-radius: 0 !important" not in css
    assert "* {\n  border-radius: 0" not in css

    allowed_files = {
        "components/ui/bmsStyle.ts",
        "components/QuickViewer.tsx",
        "components/AnalyticsDashboard.tsx",
        "components/NGSToolkit.tsx",
        "components/BioXpCockpit.tsx",
    }
    offenders: list[str] = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".css"}:
            continue
        rel = path.relative_to(FRONTEND_SRC).as_posix()
        if rel in allowed_files:
            continue
        text = read(path)
        if "rounded-none" in text or "border-radius: 0" in text or "borderRadius: 0" in text:
            offenders.append(rel)

    assert offenders == []

    quick_viewer = read(FRONTEND_SRC / "components" / "QuickViewer.tsx")
    analytics = read(FRONTEND_SRC / "components" / "AnalyticsDashboard.tsx")
    assert "BMS_FULLSCREEN_FLUSH" in quick_viewer
    assert "BMS_FULLSCREEN_FLUSH" in analytics


def test_font_family_is_owned_by_global_theme_only() -> None:
    """Components may use semantic font weights/mono spans, but not define competing app fonts."""
    offenders: list[str] = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx", ".css"}:
            continue
        rel = path.relative_to(FRONTEND_SRC).as_posix()
        if rel in {"index.css", "components/NGSToolkit.tsx", "components/MolBioToolkit/RnaStructureViewer.tsx"}:
            # Canvas/SVG drawing code needs explicit font metadata; app shell typography stays global.
            continue
        text = read(path)
        if "font-family" in text or "fontFamily" in text or "font-[" in text:
            offenders.append(rel)

    assert offenders == []
