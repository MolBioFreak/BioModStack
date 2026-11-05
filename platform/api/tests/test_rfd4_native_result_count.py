"""Jobs-list output count for native-only RFD4 publications (no synthetic Design rows)."""
import pytest
from database import Job, JobArtifact
from test_remote_lifecycle_gaps import store  # noqa: F401
from services.rfd4_proteina_results import native_result_counts


@pytest.mark.asyncio
async def test_native_result_counts_reads_registered_manifest_records(store):
    async with store() as session:
        session.add(JobArtifact(
            id='a' * 36, owner_job_id='job', attempt=0,
            logical_path='collected/rfd4_proteina/manifest.json', storage_path='/x',
            sha256='0' * 64, bytes=1, media_type='application/octet-stream',
            provenance={'native_generation_manifest': {'records': [{'a': 1}, {'a': 2}]}}))
        await session.commit()
    async with store() as session:
        assert await native_result_counts(session, ['job']) == {'job': 2}
        # Unpublished Jobs stay absent, never zero-filled or inferred.
        assert await native_result_counts(session, ['other']) == {}
        assert await native_result_counts(session, []) == {}
