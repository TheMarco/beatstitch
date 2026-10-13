"""Lightweight packages carry runtimes and licenses, never model weights."""
import json
from pathlib import Path
import tempfile
import types
import unittest

from build_desktop import (bundled_data_options, is_weight_file, model_packs_manifest,
                           resolve_packaging_options, write_model_packs_manifest)
from studio_package_check import find_bundled_weights, is_lightweight, load_model_packs


class ManifestTests(unittest.TestCase):
    def test_default_marker_is_lightweight_with_both_runtimes(self):
        self.assertEqual(model_packs_manifest(),
                         {'schema': 1, 'bundled_models': False,
                          'features': {'sections': True, 'separation': True}})

    def test_offline_and_omitted_runtime_markers(self):
        self.assertTrue(model_packs_manifest(bundle_models=True)['bundled_models'])
        self.assertEqual(model_packs_manifest(sections=False, separation=False)['features'],
                         {'sections': False, 'separation': False})

    def test_written_marker_round_trips_as_lightweight(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_model_packs_manifest(Path(folder))
            self.assertEqual(path.name, 'model-packs.json')
            self.assertEqual(json.loads(path.read_text())['bundled_models'], False)
            self.assertTrue(is_lightweight(folder))
            self.assertEqual(load_model_packs(folder)['schema'], 1)

    def test_compat_flags_and_without_flags(self):
        base = dict(with_sections=False, with_separation=False, without_sections=False,
                    without_separation=False, bundle_models=False)
        self.assertEqual(resolve_packaging_options(types.SimpleNamespace(**base)), (True, True, False))
        base.update(with_sections=True, with_separation=True)
        self.assertEqual(resolve_packaging_options(types.SimpleNamespace(**base)), (True, True, False))
        base.update(without_sections=True, bundle_models=True)
        self.assertEqual(resolve_packaging_options(types.SimpleNamespace(**base)), (False, True, True))


class DataOptionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ('assets/models', 'assets/key'):
            (self.root / folder).mkdir(parents=True)
        (self.root / 'assets/models/Beat-This-LICENSE.txt').write_text('license')
        (self.root / 'assets/models/beat-this-final0.ckpt').write_bytes(b'weight')
        (self.root / 'assets/key/LICENSE.openkeyscan').write_text('license')
        (self.root / 'assets/key/openkeyscan3.pt').write_bytes(b'weight')
        (self.root / 'assets/key/key_cnn.pkl').write_bytes(b'legacy weight')

    def test_weight_classification(self):
        self.assertTrue(is_weight_file('model.safetensors'))
        self.assertFalse(is_weight_file('LICENSE.txt'))

    def test_default_bundles_licenses_but_no_weights(self):
        joined = ' '.join(bundled_data_options(self.root))
        self.assertIn('Beat-This-LICENSE.txt', joined)
        self.assertIn('LICENSE.openkeyscan', joined)
        for weight in ('beat-this-final0.ckpt', 'openkeyscan3.pt', 'key_cnn.pkl'):
            self.assertNotIn(weight, joined)

    def test_bundle_models_includes_verified_weight_paths(self):
        joined = ' '.join(bundled_data_options(self.root, bundle_models=True))
        self.assertIn('beat-this-final0.ckpt', joined)
        self.assertIn('openkeyscan3.pt', joined)


class AbsenceGateTests(unittest.TestCase):
    def test_unexpected_weight_fails_absence_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'assets/models').mkdir(parents=True)
            (root / 'assets/models/PANNS-LICENSE.MIT').write_text('license')
            self.assertEqual(find_bundled_weights(root), [])
            (root / 'assets/sections').mkdir(parents=True)
            (root / 'assets/sections/model.safetensors').write_bytes(b'weight')
            self.assertEqual(find_bundled_weights(root), ['assets/sections/model.safetensors'])

    def test_legacy_package_without_marker_is_not_lightweight(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(load_model_packs(folder))
            self.assertFalse(is_lightweight(folder))


if __name__ == '__main__':
    unittest.main()
