"""A bookkeeping write must not discard valid prepared audio."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from studio_render_cache import RenderCache
from detector_pilot import file_hash


class RenderCacheTests(unittest.TestCase):
    def test_readable_audio_reused_when_access_timestamp_is_not_writable(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = RenderCache(Path(folder)); recipe = {'fingerprint':'a'*64, 'duration':1., 'start':0.}
            path = cache.destination(recipe['fingerprint']); path.parent.mkdir()
            sf.write(path,np.zeros((48000,2),dtype='float32'),48000,subtype='FLOAT')
            cache.put(recipe,{'path':str(path),'duration':1.})
            before = file_hash(path)
            with patch('studio_render_cache.os.utime',side_effect=PermissionError('read-only metadata')):
                result = cache.get(recipe)
            self.assertIsNotNone(result)
            self.assertTrue(result['cached_render'])
            self.assertEqual(file_hash(path),before)


if __name__ == '__main__': unittest.main()
