import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import librosa
import numpy as np
import soundfile as sf

import djmix
from studio_engine import Clip, Session, Track
from studio_overlap_crowding import (RATE, analyze_crowding, compare_patterns,
                                     crowding_advice, pitched_pattern, shorter_choice)


def melody(notes, rate=RATE):
    t = np.arange(rate // 2) / rate
    envelope = np.minimum(1, t / .01) * np.minimum(1, (.5 - t) / .01)
    return np.concatenate([sum(np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t * h) / h
                               for h in (1, 2, 3, 4)) * envelope * .1 for m in notes]).astype('float32')


class CrowdingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notes = [60, 64, 67, 72, 67, 64, 62, 60]
        cls.other = [67, 65, 62, 69, 65, 67, 71, 69]
        cls.a, cls.b = melody(cls.notes), melody(cls.other)
        cls.pattern = pitched_pattern(cls.a)

    def test_different_same_key_patterns_flag_but_matching_octaves_do_not(self):
        self.assertTrue(compare_patterns(self.pattern, pitched_pattern(self.b))['flagged'])
        self.assertFalse(compare_patterns(self.pattern, self.pattern)['flagged'])
        self.assertFalse(compare_patterns(self.pattern, pitched_pattern(melody([n + 12 for n in self.notes])))['flagged'])

    def test_pads_bass_percussion_and_quiet_layers_are_not_second_leads(self):
        rng = np.random.default_rng(81)
        noise = rng.normal(0, .04, RATE * 4).astype('float32')
        # Repeated broadband attacks, rather than only continuous noise.
        noise *= np.exp(-np.mod(np.arange(len(noise)) / RATE, .5) * 35)
        bass = melody([n - 24 for n in self.other])
        for name, audio in [('sustained', melody([60] * 8)), ('bass', bass),
                            ('percussion', noise), ('quiet', self.b * .02), ('silence', self.b * 0)]:
            with self.subTest(name=name):
                self.assertFalse(compare_patterns(self.pattern, pitched_pattern(audio))['flagged'])
        independent_bass = melody([n - 24 for n in self.notes])
        self.assertTrue(compare_patterns(self.pattern, pitched_pattern(self.b + independent_bass))['flagged'])
        # Conservatively removing bass harmonics can also remove a lead that
        # doubles that bassline. This known blind spot must not become a claim
        # that an unflagged mix is musically safe.
        self.assertFalse(compare_patterns(self.pattern, pitched_pattern(self.b + bass))['flagged'])

    def test_stereo_phase_and_finite_bounded_input(self):
        stereo = pitched_pattern(np.column_stack([self.b, -self.b]))
        self.assertTrue(compare_patterns(self.pattern, stereo)['flagged'])
        for audio in (np.zeros((RATE, 3)), np.zeros(11 * RATE), np.full(RATE, np.nan), np.empty(0)):
            with self.assertRaises(ValueError): pitched_pattern(audio)

    def fixture(self, root):
        session = Session(target_bpm=120, tracks={
            'a': Track('a', 'unused-a', 'A', 20, 120, 'C major'),
            'b': Track('b', 'unused-b', 'B', 20, 120, 'C major')},
            clips=[Clip('a', 0, 20), Clip('b', 0, 20, blend_bars=2)])
        # 2 s of context on either side; the overlap is exactly 2–6 s.
        for name, notes in [('outgoing', self.notes), ('incoming', self.other)]:
            y = np.pad(melody(notes, djmix.SR), (2 * djmix.SR, 2 * djmix.SR))
            sf.write(root / (name + '.wav'), np.column_stack([y, y]), djmix.SR, subtype='FLOAT')
        return session, {'folder': str(root), 'start': 14.}

    def test_rendered_overlap_cache_reuse_invalidation_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); session, report = self.fixture(root)
            result = analyze_crowding(session, 1, report, root / 'audio-cache')
            self.assertEqual(result['state'], 'possible_crowding')
            self.assertEqual(result['seconds'], 4.)
            session.tracks['a'].key = 'F# major'  # Source labels cannot override rendered evidence.
            with patch('studio_overlap_crowding.pitched_pattern', side_effect=AssertionError('cache missed')):
                self.assertEqual(analyze_crowding(session, 1, report, root / 'audio-cache'), result)
            saved = next(root.glob('crowding-*.json'))
            record = json.loads(saved.read_text()); record['analysis']['parts'][0]['audible_fraction'] = float('nan')
            saved.write_text(json.dumps(record))
            self.assertEqual(analyze_crowding(session, 1, report, root / 'audio-cache'), result)
            y, rate = sf.read(root / 'outgoing.wav', dtype='float32')
            sf.write(root / 'incoming.wav', y, rate, subtype='FLOAT')
            updated = analyze_crowding(session, 1, report, root / 'audio-cache')
            self.assertEqual(updated['state'], 'not_flagged')

    def test_context_is_excluded_and_short_overlap_is_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); session, report = self.fixture(root)
            a, rate = sf.read(root / 'outgoing.wav', dtype='float32')
            b = a.copy()
            b[:2 * rate] = np.column_stack([melody(self.other, rate)[:2 * rate]] * 2)
            sf.write(root / 'incoming.wav', b, rate, subtype='FLOAT')
            self.assertEqual(analyze_crowding(session, 1, report, root / 'audio-cache')['state'], 'not_flagged')
            session.clips[1].blend_bars = 1
            result = analyze_crowding(session, 1, report, root / 'audio-cache')
            self.assertEqual(result['state'], 'uncertain')
            self.assertIn('four seconds', crowding_advice(result))

    def test_cancel_and_changed_audio_do_not_publish_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); session, report = self.fixture(root)
            def cancel(message):
                if 'incoming' in message: raise djmix.MixError('Operation cancelled.')
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                analyze_crowding(session, 1, report, root / 'audio-cache', cancel)
            self.assertFalse(list(root.glob('crowding-*.json')))
            def replace(message):
                if message == 'Musical crowding check ready':
                    sf.write(root / 'incoming.wav', np.zeros((8 * djmix.SR, 2)), djmix.SR)
            with self.assertRaisesRegex(djmix.MixError, 'changed during'):
                analyze_crowding(session, 1, report, root / 'audio-cache', replace)
            self.assertFalse(list(root.glob('crowding-*.json')))

    def test_shorter_choice_preserves_cues_neighbors_and_protection(self):
        from tests.test_transitions import project
        session = project(); session.clips[2].protected_moments = [{'start': 6., 'end': 12., 'kind': 'drop', 'label': 'Drop'}]
        before = copy.deepcopy(session.to_dict())
        self.assertEqual(shorter_choice(session, 2), 1)
        self.assertEqual(session.to_dict(), before)
        session.clips[2].blend_bars = 1
        self.assertIsNone(shorter_choice(session, 2))


if __name__ == '__main__': unittest.main()
