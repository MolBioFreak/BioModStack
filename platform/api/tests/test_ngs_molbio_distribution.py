"""Contract checks for the installed local product boundary."""
import unittest
from types import SimpleNamespace
from fastapi import APIRouter, FastAPI, HTTPException
from product_scope import include_shared_routes


class DistributionBoundaryTests(unittest.TestCase):
    def test_shared_job_transport_does_not_publish_protein_routes(self):
        router = APIRouter()
        async def handler():
            return {}
        for path in ('', '/{job_id}', '/{job_id}/logs', '/antibody-iteration/from-designs', '/{job_id}/docking-results'):
            router.add_api_route(path, handler)
        app = FastAPI()
        include_shared_routes(app, router, prefix='/api/jobs', family='jobs')
        paths = set(app.openapi()['paths'])
        self.assertIn('/api/jobs/{job_id}/logs', paths)
        self.assertNotIn('/api/jobs/antibody-iteration/from-designs', paths)
        self.assertNotIn('/api/jobs/{job_id}/docking-results', paths)

    def test_other_models_are_rejected_before_materialization(self):
        from routers.jobs import normalize_job_request
        with self.assertRaises(HTTPException) as error:
            normalize_job_request(SimpleNamespace(model_id='protenix'))
        self.assertEqual(error.exception.status_code, 422)

    def test_live_application_contract_has_only_supported_product_routes(self):
        from main import app
        paths = set(app.openapi()['paths'])
        self.assertIn('/api/ont/runs', paths)
        self.assertIn('/api/jobs/{job_id}/logs', paths)
        for prefix in ('/api/msa', '/api/bioxp', '/api/frustrampnn', '/api/blind-pose', '/api/designs'):
            self.assertFalse(any(path.startswith(prefix) for path in paths), prefix)

    def test_rootless_client_receives_session_context_without_unrelated_secrets(self):
        import os
        from unittest.mock import patch
        from services.ont_signal_worker import _container_cli_env
        with patch.dict(os.environ, {"HOME": "/home/operator", "XDG_RUNTIME_DIR": "/run/user/1234", "PRIVATE_TOKEN": "secret"}, clear=True):
            context = _container_cli_env()
        self.assertEqual(context['HOME'], '/home/operator')
        self.assertEqual(context['XDG_RUNTIME_DIR'], '/run/user/1234')
        self.assertNotIn('PRIVATE_TOKEN', context)

    def test_installed_registry_contains_only_local_science(self):
        from model_registry import get_registry
        ids = {model.id for model in get_registry().list_models()}
        self.assertIn('nanopore', ids)
        self.assertLessEqual(ids, {'nanopore'})


if __name__ == '__main__':
    unittest.main()
