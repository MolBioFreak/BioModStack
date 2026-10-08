"""Additive global experiment-store migration; never run on the core job DB."""
VERSION = 22
NAME = "global_derived_resource_admission"
DESCRIPTION = "Target-bound derived CPU, DRAM and disk reservations under global policy"
SQL = r'''
ALTER TABLE resource_admission_policy ADD COLUMN disk_byte_limit INTEGER
    CHECK(disk_byte_limit IS NULL OR disk_byte_limit > 0);
CREATE TABLE derived_resource_reservations (
    reservation_id TEXT PRIMARY KEY NOT NULL,
    policy_id TEXT NOT NULL REFERENCES resource_admission_policy(policy_id),
    policy_version TEXT NOT NULL,
    target_id TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK(state IN ('active','retained','orphaned','released')),
    cpu_threads INTEGER NOT NULL CHECK(cpu_threads >= 0),
    dram_bytes INTEGER NOT NULL CHECK(dram_bytes >= 0),
    disk_bytes INTEGER NOT NULL CHECK(disk_bytes >= 0),
    storage_device TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    release_reason TEXT
);
CREATE INDEX ix_experiment_derived_resources_active
    ON derived_resource_reservations(policy_id, state);
CREATE TRIGGER trg_experiment_derived_resources_identity
BEFORE UPDATE OF reservation_id, policy_id, policy_version, target_id, machine_id,
    owner, token, storage_device, storage_path, receipt_json, created_at
ON derived_resource_reservations
BEGIN SELECT RAISE(ABORT, 'derived resource identity is immutable'); END;
CREATE TRIGGER trg_experiment_derived_resources_transition
BEFORE UPDATE ON derived_resource_reservations
WHEN OLD.state = 'released'
    OR NEW.cpu_threads > OLD.cpu_threads OR NEW.dram_bytes > OLD.dram_bytes
    OR NEW.disk_bytes > OLD.disk_bytes
    OR (OLD.state IN ('retained','orphaned') AND NEW.state = 'active')
BEGIN SELECT RAISE(ABORT, 'invalid derived resource transition'); END;
'''
