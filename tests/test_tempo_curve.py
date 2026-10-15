"""Elapsed-time integration, inverse seeking, and composed tempo processing."""
import math
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import djmix
from studio_cache import source_signature
from studio_engine import Clip, Track, Session, layout, render_session
from studio_tempo_curve import TempoCurve, keyframes
from studio_tempo_processing import prepare, reference_times
from studio_timing import VERSION as TIMING_VERSION


class CurveClockTests(unittest.TestCase):
    def test_order_target_uses_elapsed_duration_and_preserves_the_curve(self):
        from tests.test_order import project
        from studio_order import propose_order, OrderOptions
        from studio_tempo_curve import session_curve
        session = project(); session.tempo_enabled = True
        session.tempo_points = [{'beat': 0, 'bpm': 120}, {'beat': 2000, 'bpm': 160}]
        curve = session_curve(session)
        target = float(curve.to_output(3 * 120 - 2 * 8))
        result = propose_order(session, OrderOptions(target_seconds=target, allow_omissions=True))
        self.assertEqual(len(result['draft'].clips), 3)
        self.assertAlmostEqual(result['duration'], target, places=6)
        self.assertEqual(result['draft'].tempo_points, session.tempo_points)

    def test_constant_clock_and_endpoint_hold(self):
        reference = np.array([-2., 0., 1., 17., 90.])
        identity = TempoCurve(128)
        np.testing.assert_array_equal(identity.to_output(reference), reference)
        np.testing.assert_array_equal(identity.to_reference(reference), reference)
        slower = TempoCurve(128, [{'beat': 0, 'bpm': 120}])
        np.testing.assert_allclose(slower.to_output(reference), reference * 128 / 120)
        np.testing.assert_allclose(slower.to_reference(slower.to_output(reference)), reference)

    def test_linear_bpm_by_beats_integrates_logarithmically(self):
        curve = TempoCurve(120, [{'beat': 0, 'bpm': 120}, {'beat': 64, 'bpm': 132}])
        expected = 60 / (12 / 64) * math.log(132 / 120)
        self.assertAlmostEqual(float(curve.to_output(32)), expected, places=12)
        self.assertAlmostEqual(float(curve.to_reference(expected)), 32, places=12)
        self.assertAlmostEqual(float(curve.to_output(34)), expected + 2 * 120 / 132, places=12)
        self.assertEqual(float(curve.bpm_at(16)), 126)
        self.assertEqual(float(curve.bpm_at(-10)), 120)
        self.assertEqual(float(curve.bpm_at(90)), 132)

    def test_multiple_rises_falls_and_nearly_flat_sections_invert(self):
        curve = TempoCurve(128, [{'beat': 0, 'bpm': 126}, {'beat': 64, 'bpm': 132},
                                 {'beat': 160, 'bpm': 124}, {'beat': 256, 'bpm': 124 + 1e-10}])
        reference = np.linspace(-5, 300, 5000)
        output = curve.to_output(reference)
        self.assertTrue(np.all(np.diff(output) > 0))
        np.testing.assert_allclose(curve.to_reference(output), reference, atol=1e-10)
        frames = np.arange(0, 10000000, 1371)
        self.assertLessEqual(int(np.max(np.abs(curve.reference_frame(curve.output_frame(frames)) - frames))), 1)
        # d(output time)/d(reference time) follows the instantaneous tempo ratio.
        for seconds in (10., 40., 80., 200.):
            derivative = (curve.to_output(seconds + .001) - curve.to_output(seconds - .001)) / .002
            self.assertAlmostEqual(float(derivative), 128 / float(curve.bpm_at(seconds)), places=8)

    def test_adaptive_keyframes_match_analytic_curve_with_subsample_error(self):
        curve = TempoCurve(128, [{'beat': 0, 'bpm': 124}, {'beat': 256, 'bpm': 136}, {'beat': 512, 'bpm': 128}])
        n = round(240 * djmix.SR)
        def mapping(x): return curve.to_output(x / djmix.SR) * djmix.SR
        knots = [0, n, *(round(b * 60 / 128 * djmix.SR) for b in curve.beats if 0 < b < 512)]
        pairs = keyframes(mapping, knots)
        sample = np.linspace(0, n, 12001)
        error = np.interp(sample, pairs[:, 0], pairs[:, 1]) - mapping(sample)
        self.assertLessEqual(float(np.max(np.abs(error))), .76)
        self.assertTrue(np.all(np.diff(pairs, axis=0) > 0))
        self.assertLess(len(pairs), 3000)

    def test_invalid_curve_and_positions_are_rejected(self):
        for points in ([{'beat': 1, 'bpm': 128}], [{'beat': 0, 'bpm': 0}],
                       [{'beat': 0, 'bpm': 128}, {'beat': 0, 'bpm': 132}],
                       [{'beat': 0, 'bpm': float('nan')}], [{'beat': 0, 'bpm': True}]):
            with self.assertRaises(djmix.MixError): TempoCurve(128, points)
        with self.assertRaises(djmix.MixError): TempoCurve(128).to_output(float('inf'))


@unittest.skipUnless(djmix.tool_path('rubberband'), 'Rubber Band required')
class CurveProcessingTests(unittest.TestCase):
    def test_uncached_render_keeps_live_bands_when_output_directory_has_an_alias(self):
        from tests.test_finishing import project
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / 'real'; target.mkdir()
            alias = root / 'alias'
            try:
                alias.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest('Directory symlinks unavailable; the uncached ramp test covers Windows short paths')
            session = project(root)
            for clip in session.clips:
                clip.cue_out = 6; clip.blend_bars = 1
            maps = []; raw_audio = djmix.raw_audio; unlink = Path.unlink
            def opened(path):
                array = raw_audio(path); maps.append(array); return array
            def remove(path, *args, **kwargs):
                self.assertFalse(any(not array._mmap.closed and Path(array.filename) == path.resolve()
                                     for array in maps), 'Removed a live mapped audio buffer')
                return unlink(path, *args, **kwargs)
            with patch('djmix.raw_audio', side_effect=opened), patch.object(Path, 'unlink', remove):
                result = render_session(session, alias / 'mix.wav')
            self.assertTrue(maps)
            self.assertTrue(all(array._mmap.closed for array in maps))
            self.assertAlmostEqual(result['duration'], 6, places=4)

    def test_later_ramp_edit_keeps_earlier_sources_preview_and_assembly(self):
        from tests.test_finishing import project
        from studio_tempo_curve import session_curve
        from studio_render_cache import prepare_listening, audio_recipe
        from studio_transition_audio import pair_range, prepare_blend
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); session = project(root); cache = root / 'cache'
            session.clips.append(Clip('a', 2, 18, blend_bars=2))
            session.tempo_enabled = True
            # The third song alone reaches the final ramp. The earlier sources'
            # two-second processing context also ends before its starting point.
            session.tempo_points = [{'beat': 0, 'bpm': 116}, {'beat': 64, 'bpm': 130}, {'beat': 96, 'bpm': 132}]
            session.clips[1].transition_effect = 'echo'
            region = pair_range(session, 1)
            full = render_session(session, root / 'before.wav', cache_dir=cache)
            preview = prepare_listening(session, root / 'renders', region, audio_cache=cache)
            pair = prepare_blend(session, 1, cache)
            before = audio_recipe(session, region)['fingerprint']
            session.tempo_points[-1]['bpm'] = 144
            self.assertEqual(audio_recipe(session, region)['fingerprint'], before)
            self.assertTrue(prepare_listening(session, root / 'renders', region, audio_cache=cache)['cached_render'])
            reused_pair = prepare_blend(session, 1, cache)
            self.assertTrue(reused_pair['cached']); self.assertEqual(reused_pair['folder'], pair['folder'])
            changed = render_session(session, root / 'after.wav', cache_dir=cache)
            self.assertEqual((changed['reused_tracks'], changed['processed_tracks']), (2, 1))
            self.assertEqual(changed['effects_reused'], 1)
            self.assertGreater(changed['reused_mix_frames'], 0)
            self.assertNotEqual(changed['duration'], full['duration'])
            # A fresh uncached render must agree with the partially reused one.
            fresh = render_session(session, root / 'fresh.wav')
            actual, _ = sf.read(changed['path'], always_2d=True)
            expected, _ = sf.read(fresh['path'], always_2d=True)
            np.testing.assert_allclose(actual, expected, atol=3e-7)
            self.assertEqual(preview['fingerprint'], before)
            # Moving the affected ramp into source context must invalidate it.
            session.tempo_points[1]['beat'] = 58
            self.assertNotEqual(audio_recipe(session, region)['fingerprint'], before)

    def test_mix_preview_stems_cache_and_tracklist_share_one_clock(self):
        from tests.test_finishing import project
        from studio_tempo_curve import session_curve, output_positions
        from studio_render_cache import prepare_listening, audio_recipe
        from studio_export import tracklist_rows
        from studio_transition_audio import prepare_blend
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); session = project(root); cache = root / 'cache'
            session.tempo_enabled = True
            session.tempo_points = [{'beat': 0, 'bpm': 116}, {'beat': 64, 'bpm': 134}]
            session.fade_in_bars = session.fade_out_bars = 1
            session.clips[1].balance_levels = True
            session.clips[1].transition_effect = 'echo'
            curve = session_curve(session); reference = layout(session)
            positions = output_positions(curve, reference)
            full = render_session(session, root / 'full.wav', cache_dir=cache)
            full_audio, _ = sf.read(full['path'], always_2d=True)
            self.assertEqual(len(full_audio), positions[-1]['end'])
            region = (round(10.5 * djmix.SR), round(18.5 * djmix.SR))
            out_start, out_end = curve.output_span(*region)
            cropped = render_session(session, root / 'crop.wav', region=region, cache_dir=cache,
                                     stems={0: root / 'solo-a.wav', 1: root / 'solo-b.wav'})
            audio, _ = sf.read(cropped['path'], always_2d=True)
            a, _ = sf.read(root / 'solo-a.wav', always_2d=True); b, _ = sf.read(root / 'solo-b.wav', always_2d=True)
            self.assertEqual(len(audio), out_end - out_start)
            np.testing.assert_allclose(audio, a + b, atol=4e-7)
            trim = round(.006 * djmix.SR)
            # Solo headroom may add shared attenuation to a cropped render.
            ratio = 10 ** ((full['attenuation_db'] - cropped['attenuation_db']) / 20)
            np.testing.assert_allclose(audio[trim:-trim] * ratio, full_audio[out_start + trim:out_end - trim], atol=5e-7)
            self.assertEqual((cropped['reused_tracks'], cropped['processed_tracks']), (2, 0))
            rows = tracklist_rows(session)
            self.assertEqual(round(rows[1]['start_seconds'] * djmix.SR), positions[1]['start'])
            self.assertEqual(round(rows[-1]['end_seconds'] * djmix.SR), len(full_audio))
            recipe = audio_recipe(session, region)
            self.assertEqual(round(recipe['duration'] * djmix.SR), len(audio))
            prepared = prepare_listening(session, root / 'renders', region, audio_cache=cache)
            hit = prepare_listening(session, root / 'renders', region, audio_cache=cache)
            self.assertTrue(hit['cached_render']); self.assertEqual(prepared['fingerprint'], hit['fingerprint'])
            pair = prepare_blend(session, 1, cache)
            self.assertEqual(sf.info(Path(pair['folder']) / 'blend.wav').frames, round(pair['duration'] * djmix.SR))
            self.assertTrue(prepare_blend(session, 1, cache)['cached'])
            from studio_tempo_curve import preview_overlap
            local_a, local_b = preview_overlap(session, 1, pair['start'])
            self.assertEqual(local_b - local_a, positions[1]['incoming'])
            from studio_separation_comparison import prepare_comparison
            separation = prepare_comparison(session, {'draft': copy.deepcopy(session), 'plan': {'pair': 1}}, cache)
            self.assertEqual(sf.info(Path(separation['folder']) / 'draft.wav').frames, round(separation['duration'] * djmix.SR))
            # Extending the set must not reprocess unchanged first/second clips.
            session.fade_out_bars = 0
            session.clips.append(Clip('a', 2, 18, blend_bars=2))
            extended = render_session(session, root / 'extended.wav', cache_dir=cache)
            self.assertEqual((extended['reused_tracks'], extended['processed_tracks']), (2, 1))
            self.assertLessEqual(extended['true_peak_db'], -1)

    def test_loop_comparisons_with_a_rising_curve_remain_aligned_and_cached(self):
        from tests.test_loop_extension import LoopExtensionTests
        fixture = LoopExtensionTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.session.tempo_enabled = True
        fixture.session.tempo_points = [{'beat': 0, 'bpm': 116}, {'beat': 256, 'bpm': 132}]
        fixture.test_current_draft_actual_render_both_contexts_and_reuse()

    def test_session_roundtrip_disabled_curve_and_legacy_defaults(self):
        from studio_tempo_curve import session_curve
        session = Session(target_bpm=120, tempo_enabled=True,
                          tempo_points=[{'beat': 0, 'bpm': 124}, {'beat': 64, 'bpm': 130}])
        saved = session.to_dict(); loaded = Session.from_dict(saved)
        self.assertEqual(loaded.to_dict(), saved)
        loaded.tempo_enabled = False
        self.assertTrue(session_curve(loaded).identity)
        self.assertEqual(loaded.tempo_points, session.tempo_points)
        legacy = copy.deepcopy(saved); legacy['version'] = 11
        del legacy['tempo_enabled']; del legacy['tempo_points']
        self.assertFalse(Session.from_dict(legacy).tempo_enabled)
        saved['tempo_points'][1]['beat'] = 0
        with self.assertRaises(djmix.MixError): Session.from_dict(saved)

    def test_variable_tempo_drift_and_pitch_keep_frames_beats_and_tuning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'Reference.wav'; rate = djmix.SR
            time = np.arange(16 * rate) / rate
            # Left has short drum-like attacks; right has a known stable pitch.
            left = .45 * np.exp(-((time % .5) / .008) ** 2)
            right = .15 * np.sin(2 * np.pi * 440 * time)
            sf.write(source, np.column_stack((left, right)), rate, subtype='FLOAT')
            track = Track('source', str(source), 'Reference', 16., 120., 'A minor', cue_out=16.)
            track.timing = {'version': TIMING_VERSION, 'source': source_signature(source), 'state': 'drift',
                            'map': [[0, 0], [4, 4.1], [12, 12.1], [16, 16]]}
            clip = Clip(track.id, 2., 14., stabilize=True, transpose=True, semitones=2)
            curve = TempoCurve(120, [{'beat': 0, 'bpm': 120}, {'beat': 64, 'bpm': 132}])
            reference_start = 12.
            raw = prepare(track, clip, curve, reference_start, root / 'work', 'test', 0.)
            audio = np.fromfile(raw, dtype='<f4').reshape(-1, 2)
            end = float(reference_times(track, clip, curve, reference_start, np.array([clip.cue_out]))[0])
            start_frame, end_frame = curve.output_span(round(reference_start * rate), round(end * rate))
            self.assertEqual(len(audio), end_frame - start_frame); self.assertTrue(np.isfinite(audio).all())
            for source_time in np.arange(3., 13., .5):
                reference = reference_times(track, clip, curve, reference_start, np.array([source_time]))
                expected = round(float(curve.to_output(reference)[0]) * rate) - start_frame
                lo, hi = max(0, expected - 2400), min(len(audio), expected + 2400)
                observed = lo + int(np.argmax(np.abs(audio[lo:hi, 0])))
                self.assertLess(abs(observed - expected), .025 * rate)
            segment = audio[rate:3 * rate, 1]
            spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
            hz = np.fft.rfftfreq(len(segment), 1 / rate)[int(np.argmax(spectrum))]
            self.assertLess(abs(hz - 440 * 2 ** (2 / 12)), 1.)
            self.assertEqual(source_signature(source), track.timing['source'])


if __name__ == '__main__': unittest.main()
