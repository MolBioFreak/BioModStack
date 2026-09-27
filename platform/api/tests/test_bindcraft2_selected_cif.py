"""Selected BC2 CIF is real structure input, not PDB bytes with a new suffix."""
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from routers import jobs
from services.result_state_integrity import finalize_successful_job
from test_bindcraft2_publication import campaign

ATOM_LOOP = '''loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA . ALA B 1 1 ? 1.000 2.000 3.000 1.00 50.00 12 ALA B CA 1
ATOM 2 C CA . GLY A 2 1 ? 4.000 5.000 6.000 1.00 50.00 3 GLY A CA 1
#
'''


def real_campaign(root):
    campaign(root)
    for path in (root / '3_Ranked').glob('*.cif'):
        data = path.read_text().replace('_atom_site.id 1\n', ATOM_LOOP)
        path.write_text(data)


@pytest.mark.asyncio
async def test_projected_cif_design_is_verified_without_forcing_antibody_route(tmp_path):
    root = tmp_path / 'native'
    real_campaign(root)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            job = Job(id='bc', name='BC2', model_id='bindcraft2', mode='campaign',
                      status='running', queue_status='running', awaiting_input=False,
                      params={}, output_dir=str(root))
            session.add(job)
            await session.commit()
            result = await finalize_successful_job(job, str(root), session)
            assert result.completed and result.design_count == 1, job.error_message
            design = await session.get(Design, job.provenance['bindcraft2_native_publication']['candidates'][0]['design_id'])
            assert design.pdb_path.endswith('.cif')
            assert design.provenance['primary_target_state'] == 'stateA'
            request = jobs.AntibodyIterationLaunchRequest(source_job_id=job.id,
                        design_ids=[design.id], action='validate_boltz2')
            # A generic BC2 binder is not automatically antibody-compatible.
            with pytest.raises(HTTPException, match='not part of an antibody or nanobody'):
                await jobs.launch_antibody_iteration_from_designs(request, BackgroundTasks(), session)
            derivative = jobs._cif_selection_pdb(Path(design.pdb_path), tmp_path / 'seed.pdb')
            assert derivative.read_text().startswith('ATOM')
            assert jobs._extract_chain_records_from_structure(Path(design.pdb_path))['B'][0] == {
                'resseq': 12, 'icode': '', 'aa': 'A'}
            assert jobs._extract_chain_records_from_structure(derivative)['B'][0]['resseq'] == 12
            await jobs._validate_selected_design_owners(session, job, None, [design])
            unrepresentable = tmp_path / 'long_chain.cif'
            unrepresentable.write_text(Path(design.pdb_path).read_text().replace(
                'ALA B 1 1 ?', 'ALA LONG 1 1 ?').replace('12 ALA B CA', '12 ALA LONG CA'))
            with pytest.raises(ValueError, match='cannot be represented'):
                jobs._cif_selection_pdb(unrepresentable, tmp_path / 'invalid.pdb')
            # Selection readback detects source mutation, not a guessed derivative.
            (root / '3_Ranked/t_seq0_stateA.cif').write_text('data_corrupt\n')
            with pytest.raises(Exception, match='bytes'):
                await jobs._validate_selected_design_owners(session, job, None, [design])
    finally:
        await engine.dispose()
