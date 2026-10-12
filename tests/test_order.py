import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

import djmix
from studio_engine import Clip, Session, Track, layout
from studio_order import OrderOptions, default_options, draft_for_order, propose_order


def project(energies=(1, 5, 2, 4, 3)):
    tracks = {str(i): Track(str(i), f'{i}.wav', f'Song {i}', 120, 120, 'C minor', cue_out=120)
              for i in range(len(energies))}
    return Session(target_bpm=120, tracks=tracks,
                   clips=[Clip(k, 0, 120, energy=energy, blend_bars=4) for k, energy in zip(tracks, energies)])


class OrderTests(unittest.TestCase):
    def test_build_energy_nonmutating_preserves_audio_settings(self):
        session = project(); before = copy.deepcopy(session.to_dict())
        result = propose_order(session, OrderOptions(pace='build'))
        self.assertEqual([c.energy for c in result['draft'].clips], [1, 2, 3, 4, 5])
        self.assertEqual(session.to_dict(), before)
        original = {c.id: c for c in session.clips}
        self.assertTrue(all(c == original[c.id] for c in result['draft'].clips))
        self.assertEqual(result['duration'], layout(result['draft'])[-1]['end'] / djmix.SR)

    def test_fixed_positions_and_endpoints_are_hard_constraints(self):
        session = project(); session.clips[2].order_locked = True
        options = OrderOptions(pace='build', opener=session.clips[1].id, closer=session.clips[4].id)
        result = propose_order(session, options)
        self.assertEqual(result['order'][0], options.opener)
        self.assertEqual(result['order'][-1], options.closer)
        self.assertEqual(result['order'][2], session.clips[2].id)
        options.opener = session.clips[2].id
        with self.assertRaisesRegex(djmix.MixError, 'locked at position 3'):
            propose_order(session, options)

    def test_duration_subset_is_explicit_and_keeps_mandatory_songs(self):
        session = project()
        options = OrderOptions(target_seconds=232, opener=session.clips[0].id, closer=session.clips[-1].id)
        all_songs = propose_order(session, options)
        self.assertEqual(len(all_songs['draft'].clips), 5)
        options.allow_omissions = True
        subset = propose_order(session, options)
        self.assertEqual(subset['order'], [session.clips[0].id, session.clips[-1].id])
        self.assertEqual(subset['duration'], 232)
        self.assertEqual(len(subset['omitted']), 3)
        session.clips[2].order_locked = True
        subset = propose_order(session, options)
        self.assertEqual(subset['order'][2], session.clips[2].id)
        self.assertGreaterEqual(len(subset['order']), 4)

    def test_long_overlap_invalid_orders_are_pruned_and_drum_labels_are_bounded(self):
        session = project((1, 2, 3))
        session.clips[0].outro_percussive = True
        session.clips[2].blend_bars = 8
        draft, changes = draft_for_order(session, [session.clips[i].id for i in (0, 2, 1)], default_options(session))
        self.assertFalse(draft.clips[0].outro_percussive)
        self.assertTrue(changes)
        # Clip 1 fits the current pair, but cannot precede the 16-second incoming overlap.
        session.clips[0].cue_out = 10
        result = propose_order(session)
        self.assertEqual(result['order'][1], session.clips[1].id)
        layout(result['draft'])

    def test_unknown_energy_stays_unknown_and_cancel_is_responsive(self):
        session = project((None, None, None))
        result = propose_order(session, OrderOptions(pace='peak'))
        self.assertEqual(result['rated'], 0)
        self.assertTrue(all(c.energy is None for c in result['draft'].clips))
        def cancel(_): raise djmix.MixError('Operation cancelled.')
        with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
            propose_order(session, progress=cancel)

    def test_project_v3_roundtrip_and_old_project_defaults(self):
        session = project(); session.clips[1].order_locked = True
        session.planning = {'pace': 'build', 'opener': session.clips[0].id}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan.djmix'; session.save(path)
            self.assertEqual(Session.from_dict(json.loads(path.read_text())).to_dict(), session.to_dict())
        old = session.to_dict(); old['version'] = 2; old.pop('planning')
        for clip in old['clips']:
            clip.pop('energy'); clip.pop('order_locked')
        restored = Session.from_dict(old)
        self.assertEqual(restored.planning, {})
        self.assertTrue(all(c.energy is None and not c.order_locked for c in restored.clips))


class OrderDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop_app import Studio
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        with patch.object(Studio, 'load_bands'):
            self.w = Studio(Path(self.temp.name))
        self.addCleanup(self.w.close)
        self.w.session = project(); self.w.refresh()
        def synchronous(work, finished, **kwargs):
            try: result = work(lambda _: None, lambda _: None)
            except Exception as error: kwargs['failed_callback'](str(error))
            else: finished(result)
        self.job = patch.object(self.w, 'job', side_effect=synchronous); self.job.start(); self.addCleanup(self.job.stop)

    def test_draft_can_be_heard_without_mutation_and_apply_is_one_undo(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict())
        w.propose_order(); dialog = w.order_dialog
        dialog.pace.setCurrentIndex(dialog.pace.findData('build'))
        dialog.suggest()
        self.assertEqual(w.session.to_dict(), before)
        self.assertTrue(dialog.listen.isEnabled())
        self.assertIn('All songs are included', dialog.details.toPlainText())
        dialog.commit()
        self.assertEqual([c.energy for c in w.session.clips], [1, 2, 3, 4, 5])
        self.assertEqual(w.history[-1]['action'], 'Apply song order plan')
        w.undo(); self.assertEqual(w.session.to_dict(), before)

    def test_changed_controls_invalidate_proposal_and_stale_apply_is_rejected(self):
        w = self.w; w.propose_order(); dialog = w.order_dialog
        dialog.suggest(); self.assertTrue(dialog.apply.isEnabled())
        dialog.locks[w.session.clips[1].id].setChecked(True)
        self.assertIsNone(dialog.proposal); self.assertFalse(dialog.apply.isEnabled())
        dialog.suggest(); w.revision += 1
        dialog.commit()
        self.assertIn('mix changed', dialog.status.text())
        self.assertEqual(w.history, [])
        dialog.reject()

    def test_automatic_estimates_are_labeled_and_can_be_disabled_or_overridden(self):
        w = self.w
        for clip in w.session.clips: clip.energy = None
        before = copy.deepcopy(w.session.to_dict())
        w.propose_order(); dialog = w.order_dialog
        estimates = {c.id: {'value': 4., 'reason': 'Test audio activity'} for c in w.session.clips}
        with patch('studio_order_dialog.estimate_mix_energy', return_value=estimates) as analyze:
            dialog.suggest()
            self.assertEqual(analyze.call_count, 1)
            self.assertTrue(all(label.text() == 'Estimated 4/5' for label in dialog.energy_labels.values()))
            self.assertEqual(w.session.to_dict(), before)
            dialog.auto_energy.setChecked(False)
            self.assertFalse(dialog.apply.isEnabled())
            dialog.suggest(); self.assertEqual(analyze.call_count, 1)
            self.assertEqual(dialog.proposal['estimated'], 0)
            first = w.session.clips[0].id
            dialog.ratings[first].setCurrentIndex(2)
            dialog.suggest()
            self.assertEqual(dialog.proposal['energy'][first]['origin'], 'manual')
            self.assertEqual(dialog.energy_labels[first].text(), 'Your rating')
        dialog.reject()

    def test_cancelled_generation_does_not_publish_late_result(self):
        w = self.w
        with patch.object(w, 'job') as job:
            w.propose_order(); dialog = w.order_dialog; dialog.suggest()
        finished = job.call_args.args[1]
        dialog.reject(); finished(propose_order(w.session))
        self.assertIsNone(dialog.proposal)
        self.assertEqual(w.history, [])

    def test_preview_uses_proposal_pair_without_applying_and_close_rejects_late_audio(self):
        w = self.w; before = copy.deepcopy(w.session.to_dict())
        w.propose_order(); dialog = w.order_dialog
        dialog.pace.setCurrentIndex(dialog.pace.findData('build')); dialog.suggest()
        with patch.object(dialog, 'start_job') as job:
            dialog.preview()
        work, finished = job.call_args.args
        with patch('studio_order_dialog.prepare_blend', return_value={'folder': 'prepared'}) as prepare:
            report = work(lambda _: None, lambda _: None)
        snapshot, index = prepare.call_args.args[:2]
        self.assertEqual([c.energy for c in snapshot.clips], [1, 2, 3, 4, 5])
        self.assertEqual(index, 1)
        dialog.reject()
        with patch('studio_order_dialog.OrderAudition') as audition:
            finished(report)
        audition.assert_not_called()
        self.assertEqual(w.session.to_dict(), before)
