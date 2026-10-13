import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from detector_pilot import (atomic_json, digest, empty_reference, listening_tasks,
                            load_pilot, load_reviews, mark_predictions_exposed,
                            save_reference, source_signature, summarize)


def fixture(root):
    source = root / 'original.mp3'; source.write_bytes(b'original audio fixture')
    record = {'id': 'song', 'name': 'Song', 'source': source_signature(source),
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'duration': 120., 'audio': 'audio/song.wav', 'waveform': 'waveforms/song.npz',
              'result': 'results/song.json'}
    plan = {'version': 1, 'dataset': str(root), 'records': [record],
            'tasks': listening_tasks(record), 'purpose': 'development_pilot_only'}
    path = root / 'pilot.json'
    atomic_json(path, {**plan, 'plan_sha256': digest(plan), 'created_utc': 'fixture'})
    return path, record


class DetectorPilotTests(unittest.TestCase):
    def test_model_predictions_and_unknown_answers_never_become_accuracy(self):
        with tempfile.TemporaryDirectory() as folder:
            pilot, record = fixture(Path(folder))
            plan = load_pilot(pilot)
            atomic_json(pilot.parent / record['result'], {
                'plan_sha256': plan['plan_sha256'], 'source_sha256': record['source_sha256'],
                'stages': {s: {'status': 'completed', 'result': {}} for s in
                           ('listening_audio', 'waveform', 'source_key', 'beats', 'content', 'local_keys')}})
            save_reference(pilot, 'song-beginning', 'A', empty_reference())
            result = summarize(pilot)
            self.assertEqual(result['complete_predictions'], 1)
            self.assertEqual(result['reviewed_answers'], 0)
            self.assertIsNone(result['accuracy'])
            self.assertFalse(result['ui_promotion_allowed'])

    def test_reveal_requires_saved_answer_and_exposure_follows_song_and_reviewer(self):
        with tempfile.TemporaryDirectory() as folder:
            pilot, _ = fixture(Path(folder)); reference = empty_reference()
            reference['key'] = 'A minor'
            with self.assertRaisesRegex(ValueError, 'Save'):
                mark_predictions_exposed(pilot, 'song-source-key', 'A')
            first = save_reference(pilot, 'song-source-key', 'A', reference)
            self.assertFalse(first['prediction_exposed_in_tool'])
            mark_predictions_exposed(pilot, 'song-source-key', 'A')
            reference = empty_reference(); reference['voice'] = 'no'
            second = save_reference(pilot, 'song-middle', 'A', reference)
            third = save_reference(pilot, 'song-middle', 'B', reference)
            self.assertTrue(second['prediction_exposed_in_tool'])
            self.assertFalse(third['prediction_exposed_in_tool'])
            self.assertFalse(load_reviews(pilot)['entries'][0]['prediction_exposed_in_tool'])

    def test_changed_source_blocks_labels_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as folder:
            pilot, record = fixture(Path(folder)); reference = empty_reference()
            reference['voice'] = 'yes'
            save_reference(pilot, 'song-beginning', 'A', reference)
            before = (pilot.parent / 'references.json').read_bytes()
            Path(record['source'][0]).write_bytes(b'changed source audio')
            with self.assertRaisesRegex(ValueError, 'changed'):
                save_reference(pilot, 'song-middle', 'A', reference)
            self.assertEqual(before, (pilot.parent / 'references.json').read_bytes())

    def test_incomplete_markers_stay_partial_and_invalid_negative_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            pilot, _ = fixture(Path(folder)); reference = empty_reference()
            reference.update(timing_status='annotated', beats=[{'time': 1., 'downbeat': True}])
            entry = save_reference(pilot, 'song-beginning', 'A', reference)
            self.assertFalse(entry['reference']['timing_complete'])
            bad = copy.deepcopy(reference); bad['beats'][0]['time'] = 11.
            with self.assertRaisesRegex(ValueError, 'inside'):
                save_reference(pilot, 'song-beginning', 'A', bad)
            bad = empty_reference(); bad['timing_complete'] = True
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                save_reference(pilot, 'song-beginning', 'A', bad)

    def test_contradictory_drums_only_and_corrupt_plan_cannot_be_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            pilot, _ = fixture(Path(folder)); reference = empty_reference()
            reference.update(drums_only='yes', pitched='yes')
            with self.assertRaisesRegex(ValueError, 'drums-only'):
                save_reference(pilot, 'song-beginning', 'A', reference)
            altered = json.loads(pilot.read_text()); altered['tasks'][0]['end'] = 30.
            pilot.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, 'damaged or changed'):
                load_pilot(pilot)


if __name__ == '__main__':
    unittest.main()
