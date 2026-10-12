"""Precise overlap treatment, reusable parts and durable reversible copies."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_cache import source_signature
from studio_engine import Clip, Session, Track, layout
from studio_process import cancel_on
from studio_media import create_portable_copy
from studio_projects import read_project
from studio_separation import (STEMS, _run_worker, draft_with_copy, plan_separation,
                               prepare_separation, publish_separation, write_modified)
from studio_separation_comparison import prepare_comparison
from studio_timing import VERSION as TIMING_VERSION, corrected_time


def fake_worker(work, progress):
    request = json.loads((work / 'request.json').read_text())
    with sf.SoundFile(work / 'decoded.wav') as file:
        file.seek(request['start']); audio = file.read(request['end'] - request['start'], dtype='float32', always_2d=True)
    for i, name in enumerate(STEMS): sf.write(work / (name + '.wav'), audio * ((i + 1) / 10), djmix.SR, subtype='FLOAT')
    return {'frames': len(audio), 'rate': djmix.SR, 'channels': 2}


class SeparationCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / 'Source.wav'; self.cache = self.root / 'audio-cache'
        time = np.arange(24 * djmix.SR, dtype=np.float32) / djmix.SR
        audio = np.column_stack((.15 * np.sin(2 * np.pi * 80 * time), .12 * np.cos(2 * np.pi * 240 * time)))
        sf.write(self.path, audio, djmix.SR, subtype='FLOAT')
        track = Track('song', str(self.path), 'Source', 24., 120., 'A minor', cue_out=24.)
        self.session = Session(target_bpm=128., tracks={'song': track}, clips=[Clip('song', 0, 24, blend_bars=2) for _ in range(2)])
        installed = patch('studio_separation.available', return_value=True); installed.start(); self.addCleanup(installed.stop)

    def test_overlap_uses_inverse_accepted_timing_and_preserves_arrangement(self):
        track = self.session.tracks['song']; clip = self.session.clips[1]; clip.stabilize = True; clip.cue_in = 3.
        track.timing = {'version': TIMING_VERSION, 'source': source_signature(self.path), 'state': 'drift',
                        'map': [[0, 0], [3, 3.1], [12, 12.2], [24, 24]]}
        plan = plan_separation(self.session, 1, 'intro')
        span = corrected_time(track, clip, plan['end'] / djmix.SR) - corrected_time(track, clip, plan['start'] / djmix.SR)
        self.assertAlmostEqual(span, 4., places=4)
        replacement = copy.deepcopy(track); replacement.id = 'copy'
        candidate = draft_with_copy(self.session, 1, replacement)
        self.assertEqual(layout(candidate), layout(self.session))
        self.assertTrue(candidate.clips[1].stabilize)
        self.assertEqual(candidate.clips[0], self.session.clips[0])
        with self.assertRaises(djmix.MixError): plan_separation(self.session, 0, 'intro')
        clip.protected_moments = [{'start': 3.5, 'end': 5., 'kind': 'passage', 'label': 'Keep voice'}]
        with self.assertRaises(djmix.MixError): plan_separation(self.session, 1, 'intro')

    def test_only_overlap_samples_change_and_edges_return_to_original(self):
        source, _ = sf.read(self.path, dtype='float32', always_2d=True)
        plan = plan_separation(self.session, 0, 'outro', 'drums', 100)
        a, b = plan['start'], plan['end']; stems = {n: np.zeros((b - a, 2), dtype='float32') for n in STEMS}
        target = self.root / 'result.wav'; write_modified(self.path, target, stems, plan)
        result, _ = sf.read(target, dtype='float32', always_2d=True)
        np.testing.assert_array_equal(result[:a], source[:a]); np.testing.assert_array_equal(result[b:], source[b:])
        np.testing.assert_array_equal(result[[a, b - 1]], source[[a, b - 1]])
        np.testing.assert_array_equal(result[a + plan['fade']:b - plan['fade']], 0.)

    def test_parts_reused_for_amount_mode_and_durable_copy_survives_roundtrip(self):
        original = copy.deepcopy(self.session.to_dict())
        with patch('studio_separation._run_worker', side_effect=fake_worker) as worker:
            first = prepare_separation(self.session, 1, 'intro', 'instrumental', 75, self.cache)
            second = prepare_separation(self.session, 1, 'intro', 'drums', 50, self.cache)
            self.assertEqual(worker.call_count, 1)
        self.assertEqual(first['parts_folder'], second['parts_folder']); self.assertNotEqual(first['folder'], second['folder'])
        with patch('studio_separation._run_worker', side_effect=AssertionError('Unnecessary inference')):
            reused = prepare_separation(self.session, 1, 'intro', 'instrumental', 75, self.cache)
        self.assertEqual(reused['copy'].path, first['copy'].path)
        replacement = publish_separation(first, self.root / 'media')
        draft = draft_with_copy(self.session, 1, replacement)
        restored = Session.from_dict(json.loads(json.dumps(draft.to_dict())))
        self.assertEqual(restored.to_dict(), draft.to_dict())
        self.assertEqual(layout(restored), layout(self.session))
        portable = create_portable_copy(draft, self.root / 'portable')
        moved = self.root / 'moved'; (self.root / 'portable').rename(moved)
        reopened, _ = read_project(moved / Path(portable['project']).name)
        self.assertTrue(Path(reopened.tracks[replacement.id].path).is_file())
        self.assertEqual(self.session.to_dict(), original)
        Path(replacement.path).write_bytes(b'User modified copy')
        with self.assertRaisesRegex(djmix.MixError, 'will not be overwritten'): publish_separation(first, self.root / 'media')
        self.assertEqual(Path(replacement.path).read_bytes(), b'User modified copy')

    def test_real_mix_comparison_preserves_timing_and_reuses_unchanged_render(self):
        track = self.session.tracks['song']
        track.timing = {'version': TIMING_VERSION, 'source': source_signature(self.path), 'state': 'drift',
                        'map': [[0, 0], [3, 3.1], [12, 12.2], [24, 24]]}
        self.session.clips[1].stabilize = True
        self.session.clips[1].transpose = True; self.session.clips[1].semitones = 1
        with patch('studio_separation._run_worker', side_effect=fake_worker):
            result = prepare_separation(self.session, 1, 'intro', 'instrumental', 100, self.cache)
        self.assertEqual(result['copy'].timing['source'], result['copy_signature'])
        self.assertTrue(result['draft'].clips[1].stabilize)
        self.assertEqual(result['draft'].clips[1].shift, 1)
        comparison = prepare_comparison(self.session, result, self.cache)
        for name in ('current', 'draft'):
            info = sf.info(Path(comparison['folder']) / (name + '.wav'))
            self.assertEqual((info.frames, info.channels), (comparison['frames'], 2))
            self.assertGreater(max(comparison[name + '_wave']), 0)
        with patch('studio_separation_comparison.prepare_listening', side_effect=AssertionError('Unnecessary rendering')):
            self.assertEqual(prepare_comparison(self.session, result, self.cache)['folder'], comparison['folder'])

    def test_cancelled_preparation_never_invokes_separator(self):
        stop = threading.Event(); stop.set()
        with cancel_on(stop), patch('studio_separation._run_worker', side_effect=AssertionError('Must not run')):
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                prepare_separation(self.session, 1, 'intro', 'drums', 100, self.cache)

    def test_cancelling_live_worker_reaps_the_process(self):
        script = "import pathlib,json,sys,time; p=pathlib.Path(sys.argv[1]); (p/'progress.json').write_text(json.dumps('Ready')); time.sleep(120)"
        stop = threading.Event(); processes = []; original = subprocess.Popen
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs); processes.append(process); return process
        with patch('studio_separation.worker_command', return_value=[sys.executable, '-c', script]), patch('studio_separation.subprocess.Popen', side_effect=spawn), cancel_on(stop):
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'): _run_worker(self.root, lambda _: stop.set())
        self.assertEqual(len(processes), 1); self.assertIsNotNone(processes[0].poll())


if __name__ == '__main__': unittest.main()
