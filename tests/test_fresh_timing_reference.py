"""Mock tests: no model inference, no real archives."""
import io, unittest, zipfile
import numpy as np
import prototype_fresh_timing_reference as m


def _specs(ids): return {r: (f"{r}.npy", f"sha-{r}") for r in ids}
def _anns(ids): return {r: (f"{r}.beats", f"ann-{r}") for r in ids}
def _npy_bytes():
    b = io.BytesIO(); np.save(b, np.zeros((100, 128), np.float32)); return b.getvalue()


class FreshTimingTests(unittest.TestCase):
    def test_actual_archive_and_annotation_paths(self):
        self.assertEqual(m.recording_id('gtzan_blues_00000/track.npy'), 'gtzan_blues_00000')
        self.assertEqual(m.recording_id('beats/gtzan_blues_00000.beats'), 'gtzan_blues_00000')
        self.assertEqual(m.recording_id('genres/blues/blues.00000.wav'), 'gtzan_blues_00000')

    def test_nearest_greedy_counterexample_keeps_both_matches(self):
        pairs, fp, fn = m.match_events([1., 1.04], [1.03, 1.07], .035)
        self.assertEqual((len(pairs), fp, fn), (2, 0, 0))
        score = m.score_events(np.array([1., 1.04]), np.array([1.03, 1.07]), .035)
        self.assertEqual(score['f1'], 1.)

    def test_duplicate_connected_component_cannot_bypass_observed_artist(self):
        ids = ['gtzan_rock_00001','gtzan_rock_00002','gtzan_rock_00003']
        specs = {ids[0]:('a.npy','same'),ids[1]:('b.npy','same'),ids[2]:('c.npy','different')}
        artists = {ids[0]:('Old artist','a'),ids[1]:('New artist','b'),ids[2]:('New artist','c')}
        records, excluded = m.build_records(specs,_anns(ids),artists,{ids[0]},{'old artist'})
        self.assertFalse(records)
        self.assertEqual(len(excluded),3)

    def test_ambiguous_artist_is_descriptive_only(self):
        ids=['gtzan_pop_00001','gtzan_rock_00002']
        artists={ids[0]:('Unknown','a'),ids[1]:('Someone (?)','b')}
        records,_=m.build_records(_specs(ids),_anns(ids),artists,set(),set())
        self.assertTrue(all(r['split']=='descriptive_only' for r in records))

    def test_observed_id_and_artist_excluded(self):
        ids = ["gtzan_blues_00000", "gtzan_blues_00099", "gtzan_rock_00001"]
        artists = {ids[0]: ("John Lee Hooker", "a"), ids[1]: ("  JOHN   lee Hooker ", "b"),
                   ids[2]: ("New Artist", "c")}
        recs, exc = m.build_records(_specs(ids), _anns(ids), artists,
                                    {"gtzan_blues_00000"}, {"john lee hooker"})
        self.assertEqual([r["id"] for r in recs], ["gtzan_rock_00001"])
        self.assertEqual({e["reason"] for e in exc}, {"observed_id", "observed_artist_group"})

    def test_no_cross_split_group_leakage_and_unknown_descriptive(self):
        ids = [f"gtzan_rock_{i:05d}" for i in range(20)] + ["gtzan_pop_00001"]
        artists = {r: (f"Artist {i % 5}", "t") for i, r in enumerate(ids[:-1])}
        artists[ids[-1]] = ("", "t")
        recs, _ = m.build_records(_specs(ids), _anns(ids), artists, set(), set())
        by_group = {}
        for r in recs: by_group.setdefault(r["group"], set()).add(r["split"])
        self.assertTrue(all(len(v) == 1 for v in by_group.values()))
        self.assertEqual([r for r in recs if r["id"] == ids[-1]][0]["split"], "descriptive_only")

    def test_duplicate_spectrogram_same_group(self):
        ids = ["gtzan_rock_00001", "gtzan_rock_00002", "gtzan_rock_00003"]
        specs = {ids[0]: ("a.npy", "same"), ids[1]: ("b.npy", "same"), ids[2]: ("c.npy", "diff")}
        artists = {r: (f"A{i}", "t") for i, r in enumerate(ids)}
        recs, _ = m.build_records(specs, _anns(ids), artists, set(), set())
        self.assertEqual(recs[0]["group"], recs[1]["group"])
        self.assertEqual(recs[0]["split"], recs[1]["split"])

    def test_malformed_and_out_of_bounds_labels_explicit(self):
        for raw in (b"1.0\n", b"1.0 1\n1.0 2\n", b"29.0 1\n31.0 2\n"):
            with self.assertRaises(ValueError): m.parse_beats(raw, 30.)
        self.assertEqual(m.parse_beats(b"0.5 1\n1.0 2\n", 30.), [(0.5, 1), (1.0, 2)])

    def test_one_to_one_matching_extra_and_missed(self):
        pairs, fp, fn = m.match_events([1., 2.], [1.01, 1.02, 2.01], .03)
        self.assertEqual((len(pairs), fp, fn), (2, 1, 0))
        s = m.score_events([1., 2.], [1.01], .07)
        self.assertEqual((s["matched"], s["missed"]), (1, 1)); self.assertEqual(s["recall"], .5)
        self.assertEqual(m.score_events([1.], [], .07)["precision"], 0.)

    def test_four_beat_subset_not_malformed(self):
        self.assertTrue(m.is_four_beat([(0., 1), (.5, 2), (1., 3), (1.5, 4), (2., 1)]))
        self.assertFalse(m.is_four_beat([(0., 1), (.5, 2), (1., 3), (1.5, 1)]))

    def test_failed_recording_retained(self):
        class S:
            @staticmethod
            def _peak_values(logits, times): return np.full(len(times), .9)
            @staticmethod
            def coherent_grid(*a): return dict(beats=[], downbeats=[], state="uncertain", coverage=0.)
        def bad(spect): raise RuntimeError("boom")
        raw_spec = _npy_bytes(); raw_ann = b'0.5 1\n1.0 2\n'
        rec = dict(id="gtzan_rock_00001", split="test", group="a",
                   spectrogram=("x.npy", m.sha256_bytes(raw_spec)), annotation=("x.beats", m.sha256_bytes(raw_ann)))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x.npy", raw_spec); z.writestr("x.beats", raw_ann)
        buf.seek(0)
        with zipfile.ZipFile(buf) as z: row = m.run_one(rec, z, z, bad, S)
        self.assertEqual(row["status"], "failed"); self.assertIn("boom", row["error"])
        self.assertEqual(m.pooled([row]), {})


if __name__ == "__main__": unittest.main()
