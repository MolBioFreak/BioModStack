from __future__ import annotations

import importlib
import sqlite3


def load_router(monkeypatch, tmp_path):
    monkeypatch.setenv('BMS_RUNTIME_MODE', 'development')
    monkeypatch.setenv('BMS_STATE_DIR', str(tmp_path))
    from routers import dev_issues

    return importlib.reload(dev_issues)


def create(router, body: str, lane: str):
    return router.create_dev_issue(router.DevIssueCreate(
        body=body,
        lane=lane,
        scope_kind='page',
        scope_key='page:dashboard',
        page_label='Dashboard',
        route='/',
        author_kind='operator',
    ))


def test_mobile_lane_is_persisted_and_filterable(monkeypatch, tmp_path):
    router = load_router(monkeypatch, tmp_path)
    create(router, 'desktop issue', 'general')
    mobile = create(router, 'mobile issue', 'mobile')

    result = router.list_dev_issues(status='active', lane='mobile', scope_key=None, limit=100)

    assert mobile.lane == 'mobile'
    assert [issue.body for issue in result.items] == ['mobile issue']
    assert result.active_count == 1


def test_existing_issue_database_migrates_to_general_lane(monkeypatch, tmp_path):
    router = load_router(monkeypatch, tmp_path)
    database = tmp_path / 'dev-issues.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute(
            '''
            CREATE TABLE dev_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('open', 'in_progress', 'cleared')),
                scope_kind TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                page_label TEXT NOT NULL,
                route TEXT NOT NULL,
                component_hint TEXT,
                author_kind TEXT NOT NULL,
                frontend_revision TEXT,
                api_revision TEXT NOT NULL,
                created_at TEXT NOT NULL,
                cleared_at TEXT,
                resolution_note TEXT
            )
            '''
        )
        connection.execute(
            '''
            INSERT INTO dev_issues (
                body, status, scope_kind, scope_key, page_label, route,
                component_hint, author_kind, frontend_revision, api_revision,
                created_at, cleared_at, resolution_note
            ) VALUES ('existing', 'open', 'page', 'page:dashboard', 'Dashboard', '/',
                      NULL, 'operator', NULL, 'old', '2026-08-27T00:00:00Z', NULL, NULL)
            '''
        )

    result = router.list_dev_issues(status='active', lane='general', scope_key=None, limit=100)

    assert len(result.items) == 1
    assert result.items[0].lane == 'general'