import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import librosa
import numpy as np
import soundfile as sf

import djmix
from studio_engine import Clip, Session, Track
from studio_overlap_harmony import (RATE, advice_text, analyze_overlap, compare_sides,
                                    summarize_side, tonal_evidence)


def progression(shift=0):
    chords = [(48, 60, 64, 67), (53, 60, 65, 69), (55, 59, 62, 67), (48, 60, 64, 67)]
    time = np.arange(RATE * 2) / RATE
    return np.concatenate([sum(np.sin(2*np.pi*440*2**((m+shift-69)/12)*time) for m in chord) / 4
                           for chord in chords]).astype(np.float32)


class OverlapHarmonyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audio = [progression(i) for i in (0, 1, 7)]
        cls.evidence = [tonal_evidence(a) for a in cls.audio]

    def test_local_progressions_related_and_clashing(self):
        c, sharp, g = [summarize_side([part]) for part in self.evidence]
        self.assertEqual(c['keys'], ['C major'])
        self.assertEqual(sharp['keys'], ['C# major'])
        self.assertEqual(g['keys'], ['G major'])
        self.assertEqual(compare_sides(c, g)['state'], 'related')
        self.assertEqual(compare_sides(c, sharp)['state'], 'possible_clash')

    def test_silence_noise_single_note_and_short_overlap_are_uncertain(self):
        time = np.arange(RATE * 8) / RATE
        note = tonal_evidence(np.sin(2*np.pi*261.63*time))
        self.assertTrue(note['tonal']); self.assertEqual(note['state'], 'uncertain')
        noise = tonal_evidence(np.random.default_rng(4).normal(0, .1, len(time)))
        self.assertFalse(noise['tonal']); self.assertEqual(noise['state'], 'uncertain')
        self.assertEqual(tonal_evidence(np.zeros_like(time))['state'], 'silent')
        self.assertEqual(tonal_evidence(self.audio[0][:RATE * 2])['state'], 'uncertain')
        with self.assertRaises(ValueError): tonal_evidence(np.full(RATE * 8, np.nan))

    def test_stereo_polarity_quiet_tail_and_changing_keys(self):
        audio = self.audio[0] * .003
        self.assertEqual(tonal_evidence(np.column_stack([audio, -audio]))['keys'], ['C major'])
        changing = summarize_side([self.evidence[0], self.evidence[1]])
        self.assertEqual(changing['state'], 'uncertain')
        self.assertTrue(changing['tonal'])
        c = summarize_side([self.evidence[0]])
        self.assertEqual(compare_sides(c, changing)['state'], 'uncertain')

    def prepared(self, root):
        # Both whole-song labels are deliberately wrong; only rendered overlap counts.
        session = Session(target_bpm=120, tracks={
            'a': Track('a', 'unused-a.wav', 'Outgoing', 16, 120, 'F# minor'),
            'b': Track('b', 'unused-b.wav', 'Incoming', 16, 120, 'F# minor')},
            clips=[Clip('a', 0, 16), Clip('b', 0, 16, blend_bars=4)])
        c, sharp, g = [librosa.resample(a, orig_sr=RATE, target_sr=djmix.SR) for a in self.audio]
        silence = np.zeros_like(c)
        folder = root / 'audio-cache/transitions' / ('a' * 64); folder.mkdir(parents=True)
        for name, audio in [('outgoing', np.r_[sharp, c, silence]), ('incoming', np.r_[silence, g, sharp])]:
            sf.write(folder / f'{name}.wav', np.column_stack([audio] * 2), djmix.SR, subtype='FLOAT')
        return session, {'folder': str(folder), 'start': 0., 'end': 24., 'duration': 24.}

    def test_exact_rendered_overlap_cache_and_replaced_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); session, report = self.prepared(root)
            before = copy.deepcopy(session.to_dict())
            result = analyze_overlap(session, 1, report, root / 'audio-cache')
            self.assertEqual(result['seconds'], 8)
            self.assertEqual(result['outgoing']['keys'], ['C major'])
            self.assertEqual(result['incoming']['keys'], ['G major'])
            self.assertEqual(result['state'], 'related')
            self.assertEqual(session.to_dict(), before)
            with patch('studio_overlap_harmony._analyze_stem', side_effect=AssertionError('Must reuse')):
                self.assertEqual(analyze_overlap(session, 1, report, root / 'audio-cache'), result)
            sharp = librosa.resample(self.audio[1], orig_sr=RATE, target_sr=djmix.SR)
            new = np.r_[np.zeros_like(sharp), sharp, np.zeros_like(sharp)]
            sf.write(Path(report['folder']) / 'incoming.wav', np.column_stack([new] * 2), djmix.SR, subtype='FLOAT')
            changed = analyze_overlap(session, 1, report, root / 'audio-cache')
            self.assertEqual(changed['state'], 'possible_clash')
            session.clips[1].intro_percussive = True
            self.assertIn('marked drum intro', advice_text(changed, session, 1))

    def test_cancellation_does_not_publish_a_partial_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); session, report = self.prepared(root)
            def cancel(message):
                if message.startswith('Incoming'): raise djmix.MixError('Operation cancelled.')
            with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                analyze_overlap(session, 1, report, root / 'audio-cache', cancel)
            self.assertEqual(list(Path(report['folder']).glob('harmony-*.json')), [])


if __name__ == '__main__': unittest.main()
