from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import djmix
from studio_energy import RATE, analyze_clip, audio_features, estimate_mix_energy, summarize
from studio_order import OrderOptions, propose_order
from studio_storage import StorageManager
from tests.test_order import project


def percussion(density):
    t = np.arange(RATE * 12) / RATE
    phase = np.mod(t, 1 / density)
    return (np.sin(2 * np.pi * 70 * t) * np.exp(-phase * 30)
            + np.random.default_rng(7).standard_normal(len(t)) * np.exp(-phase * 65) * .3)


class EnergyTests(unittest.TestCase):
    def test_activity_gain_and_stereo_polarity(self):
        sparse, busy = percussion(1), percussion(6)
        low = summarize([audio_features(sparse)])['value']
        result = summarize([audio_features(busy)])
        self.assertGreater(result['value'], low + 1)
        for audio in (busy * .02, np.column_stack([busy, -busy])):
            self.assertAlmostEqual(summarize([audio_features(audio)])['value'], result['value'], places=5)
        slow = summarize([audio_features(busy)], .75)['value']
        self.assertLess(slow, result['value'])

    def test_silence_short_audio_and_invalid_samples_stay_unknown(self):
        self.assertIsNone(summarize([audio_features(np.zeros(RATE * 12))])['value'])
        self.assertIsNone(summarize([audio_features(percussion(6)[:RATE * 4])])['value'])
        with self.assertRaises(ValueError):
            audio_features(np.full(RATE * 12, np.nan))

    def test_cache_is_per_source_and_cues_and_storage_manages_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'song.wav'; source.write_bytes(b'original')
            cache = root / 'audio-cache'
            raw = np.column_stack([percussion(3)] * 2).astype('<f4').tobytes()
            with patch('studio_energy.djmix.ffmpeg', return_value=SimpleNamespace(stdout=raw)) as decoder:
                first = analyze_clip(source, 0, 12, cache)
                messages = []
                self.assertEqual(first, analyze_clip(source, 0, 12, cache, messages.append))
                self.assertEqual(decoder.call_count, 1)
                self.assertIn('Reusing energy analysis', messages)
                analyze_clip(source, 1, 13, cache)
                source.write_bytes(b'changed source')
                analyze_clip(source, 0, 12, cache)
                self.assertEqual(decoder.call_count, 3)
            files = list((cache / 'energy').glob('*.json'))
            entries = StorageManager(root).entries()
            self.assertEqual({p.resolve() for p in files}, {e['path'].resolve() for e in entries})
            self.assertTrue(all(e['group'] == 'Analysis and waveforms' for e in entries))
            interrupted = cache / 'energy' / ('a' * 64 + '.abcde_12.tmp')
            interrupted.write_bytes(b'partial')
            self.assertIn(interrupted.resolve(), [e['path'] for e in StorageManager(root).entries()])

    def test_new_song_reuses_prior_analysis_and_cancellation_does_not_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); session = project((None, None, None))
            for track in session.tracks.values():
                path = root / f'{track.id}.wav'; path.write_bytes(b'source'); track.path = str(path)
            for clip in session.clips: clip.cue_out = 12
            third = session.clips.pop()
            raw = np.column_stack([percussion(3)] * 2).astype('<f4').tobytes()
            with patch('studio_energy.djmix.ffmpeg', return_value=SimpleNamespace(stdout=raw)) as decoder:
                estimate_mix_energy(session, root / 'audio-cache')
                session.clips.append(third)
                result = estimate_mix_energy(session, root / 'audio-cache')
                self.assertEqual(decoder.call_count, 3)
                self.assertEqual(len(result), 3)
                def cancel(message):
                    if 'Saving' in message: raise djmix.MixError('Operation cancelled.')
                with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                    analyze_clip(session.tracks[third.track_id].path, 1, 13, root / 'audio-cache', cancel)
            self.assertEqual(len(list((root / 'audio-cache/energy').glob('*.json'))), 3)

    def test_automatic_build_manual_override_and_off(self):
        session = project((None, None, None))
        estimates = {clip.id: {'value': value, 'reason': 'Estimated activity'}
                     for clip, value in zip(session.clips, (5, 1, 3))}
        proposal = propose_order(session, OrderOptions(pace='build'), energy_estimates=estimates)
        self.assertEqual(proposal['order'], [session.clips[i].id for i in (1, 2, 0)])
        self.assertEqual((proposal['estimated'], proposal['rated']), (3, 0))
        session.clips[0].energy = 1
        proposal = propose_order(session, OrderOptions(pace='build'), energy_estimates=estimates)
        self.assertEqual(proposal['energy'][session.clips[0].id]['value'], 1)
        self.assertEqual(proposal['energy'][session.clips[0].id]['origin'], 'manual')
        off = propose_order(session, OrderOptions(auto_energy=False), energy_estimates=estimates)
        self.assertEqual((off['estimated'], off['rated']), (0, 1))
        self.assertIsNone(off['energy'][session.clips[1].id]['value'])
        self.assertEqual(off['draft'].planning['auto_energy'], False)


if __name__ == '__main__':
    unittest.main()
