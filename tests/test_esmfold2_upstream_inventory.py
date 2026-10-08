"""Probe contract tests: synthetic metadata is never scientific evidence."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'scripts/probes/esmfold2_upstream_inventory.py'
spec = importlib.util.spec_from_file_location('esmfold2_inventory', PATH)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.body = json.dumps({'esmc_id': 'biohub/ESMC-6B'}).encode()
        self.metadata = {'id': 'biohub/ESMFold2-Fast', 'sha': 'a' * 40,
                         'siblings': [self.small('config.json', self.body),
                                      {'rfilename': 'model.safetensors', 'size': 123,
                                       'lfs': {'sha256': 'b' * 64, 'size': 123}}]}

    @staticmethod
    def small(name, body):
        return {'rfilename': name, 'size': len(body), 'blobId': hashlib.sha1(
            b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()}

    def run_inventory(self, metadata=None, body=None):
        return probe.inventory('biohub/ESMFold2-Fast', 'a' * 40,
                               self.metadata if metadata is None else metadata,
                               lambda url: self.body if body is None else body)

    def test_no_weight_download_and_exact_small_identity(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return self.body
        result = probe.inventory('biohub/ESMFold2-Fast', 'a' * 40, self.metadata, fetch)
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].endswith('/config.json'))
        self.assertEqual(result['files'][0]['sha256'], hashlib.sha256(self.body).hexdigest())
        self.assertIn('not_download_verified', result['files'][1]['verification'])
        self.assertNotIn('approval_ref', result)

    def test_repository_and_revision_binding(self):
        for key in ('id', 'sha'):
            metadata = copy.deepcopy(self.metadata)
            metadata[key] = 'other'
            with self.assertRaises(ValueError): self.run_inventory(metadata)

    def test_small_bytes_tampering(self):
        with self.assertRaises(ValueError): self.run_inventory(body=b'wrong')

    def test_lfs_identity_rejection(self):
        for key, value in [('sha256', 'wrong'), ('size', 124), ('size', True)]:
            metadata = copy.deepcopy(self.metadata)
            metadata['siblings'][1]['lfs'][key] = value
            with self.assertRaises(ValueError): self.run_inventory(metadata)

    def test_unsafe_duplicate_members(self):
        for name in ('../config.json', 'config.json'):
            metadata = copy.deepcopy(self.metadata)
            metadata['siblings'][1]['rfilename'] = name
            with self.assertRaises(ValueError): self.run_inventory(metadata)

    def test_refuse_non_lfs_weight_body(self):
        del self.metadata['siblings'][1]['lfs']
        with self.assertRaises(ValueError): self.run_inventory()

    def test_esmc_requires_complete_shard_index(self):
        self.metadata['id'] = 'biohub/ESMC-6B'
        with self.assertRaisesRegex(ValueError, 'index/shard'):
            probe.inventory('biohub/ESMC-6B', 'a' * 40, self.metadata, lambda url: self.body)

    def test_redirect_handler_never_follows(self):
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org'))


if __name__ == '__main__':
    unittest.main()
