"""Research-only fresh GTZAN timing references; no app/UI changes.

Reads official precomputed 50 Hz / 128-mel spectrograms, calls the identical
production Audio2Frames.spect2frames + Postprocessor with pinned weights
(studio_structure.MODEL_SHA256), and scores raw + coherent-grid events.
"""
from __future__ import annotations
import argparse, hashlib, json, math, re, sys, zipfile, io, os
from collections import defaultdict
from pathlib import Path
from importlib import metadata

import numpy as np
from detector_pilot import atomic_json, digest

FPS, MEL, SEED = 50, 128, "DJ-fresh-timing-20261012"
TOLS = (0.03, 0.07)
ANN_PREFIX = "beat_this_annotations-1.0/gtzan/annotations/beats/"
_ID_RE = re.compile(r"(?:gtzan_)?(blues|classical|country|disco|hiphop|jazz|metal|pop|reggae|rock)[._](\d{5})(?=[/._]|$)")


def sha256_bytes(b): return hashlib.sha256(b).hexdigest()
def sha256_file(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""): h.update(c)
    return h.hexdigest()
def normalize_artist(a):
    return " ".join(str(a or "").casefold().split()).strip(" .")
def recording_id(filename):
    m = _ID_RE.search(str(filename))
    return f"gtzan_{m.group(1)}_{m.group(2)}" if m else None
def genre_number(rid):
    _, g, n = rid.split("_"); return g, n

def parse_artist_index(text):
    out = {}
    for line in text.splitlines():
        p = [x.strip() for x in line.split(":::")]
        if len(p) == 3 and p[0].endswith(".wav"):
            genre, num = Path(p[0]).stem.rsplit(".", 1)
            out[f"gtzan_{genre}_{num}"] = (p[1], p[2])
    return out

def group_for(rid, artist):
    g, _ = genre_number(rid)
    if g == "classical": return "classical:performer_unknown"
    n = normalize_artist(artist)
    if (not n or '?' in n or n in ('unknown', 'unidentified', 'n/a', 'na', 'none')
            or any(word in n for word in ('possibly', 'probably', 'unidentified'))):
        return None
    return n

def assign_splits(groups, seed=SEED):
    """Seeded 60/20/20 group split, frozen before inference (mirrors prior plan)."""
    ordered = sorted(groups, key=lambda g: hashlib.sha256(f"{seed}:{g}".encode()).hexdigest())
    a, b = round(len(ordered) * .6), round(len(ordered) * .8)
    return {g: ("fit" if i < a else "validation" if i < b else "test") for i, g in enumerate(ordered)}

def _safe_members(zf):
    for info in zf.infolist():
        n = info.filename
        if n.startswith("/") or ".." in Path(n).parts or info.is_dir(): continue
        yield info

def _member_sha(zf, info):
    h = hashlib.sha256()
    with zf.open(info) as f:
        for c in iter(lambda: f.read(1 << 20), b""): h.update(c)
    return h.hexdigest()

def discover_spectra(zf):
    """Map individual verified NPY members; never hash a bundle as a track."""
    out = {}
    for info in _safe_members(zf):
        name = info.filename
        if name.endswith(".npy"):
            rid = recording_id(name)
            if rid:
                if rid in out or info.file_size > 4_000_000:
                    raise ValueError('Duplicate recording or oversized spectrogram: ' + name)
                arr = np.load(io.BytesIO(zf.read(info)), allow_pickle=False)
                if arr.dtype.kind != 'f' or arr.ndim != 2 or arr.shape[1] != MEL or not np.isfinite(arr).all():
                    raise ValueError('Invalid spectrogram: ' + name)
                out[rid] = (name, _member_sha(zf, info))
    return out

class _BytesIO:
    def __init__(self, b): self._b, self._p = b, 0
    def read(self, n=-1):
        r = self._b[self._p:] if n < 0 else self._b[self._p:self._p + n]; self._p += len(r); return r
    def seek(self, o, w=0): self._p = o if w == 0 else self._p + o if w == 1 else len(self._b) + o; return self._p
    def tell(self): return self._p
    def seekable(self): return True
    def readable(self): return True
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass

def parse_beats(raw, duration=None):
    """Explicit validation; never silently repair malformed/out-of-bounds rows."""
    try: text = raw.decode("utf-8")
    except Exception as e: raise ValueError(f"annotation not utf-8: {e}")
    rows = []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip(): continue
        parts = line.replace(",", " ").split()
        if len(parts) != 2: raise ValueError(f"annotation line {i}: expected 'time beat'")
        try: t, b = float(parts[0]), float(parts[1])
        except ValueError: raise ValueError(f"annotation line {i}: non-numeric")
        if not math.isfinite(t) or not math.isfinite(b) or b != round(b) or not 1 <= b <= 12:
            raise ValueError(f"annotation line {i}: invalid time/beat")
        rows.append((t, int(b)))
    if not rows: raise ValueError("annotation empty")
    times = [r[0] for r in rows]
    if any(y <= x for x, y in zip(times, times[1:])): raise ValueError("annotation times not increasing")
    if times[0] < 0: raise ValueError("annotation time negative")
    if duration is not None and times[-1] >= duration:
        raise ValueError(f"annotation out of bounds: {times[-1]} >= duration {duration}")
    return rows

def is_four_beat(rows):
    beats = [b for _, b in rows]
    if max(beats) != 4: return False
    return all(beats[i + 1] == beats[i] % 4 + 1 for i in range(len(beats) - 1))

def match_events(ref, est, tol):
    """Maximum-cardinality ordered one-to-one matching for time intervals."""
    ref, est = list(map(float, ref)), list(map(float, est))
    if (not math.isfinite(tol) or tol <= 0 or not np.isfinite(ref + est).all()
            or any(y <= x for values in (ref, est) for x, y in zip(values, values[1:]))):
        raise ValueError('Expected sorted finite event times and a positive tolerance.')
    i = j = 0; pairs = []
    while i < len(ref) and j < len(est):
        if est[j] < ref[i] - tol: j += 1
        elif ref[i] < est[j] - tol: i += 1
        else: pairs.append((i, j)); i += 1; j += 1
    return pairs, len(est) - len(pairs), len(ref) - len(pairs)

def score_events(ref, est, tol):
    pairs, fp, fn = match_events(ref, est, tol)
    tp = len(pairs)
    return dict(reference_count=len(ref), predicted_count=len(est), matched=tp,
                false_positive=fp, missed=fn,
                precision=tp / len(est) if len(est) else 0., recall=tp / len(ref) if len(ref) else 0.,
                f1=2 * tp / (len(ref) + len(est)) if len(ref) + len(est) else 0.)

# ---------------- plan ----------------

def build_records(spectra, annotations, artists, observed_ids, observed_groups):
    """Pure plan builder: exclude observed IDs and every observed artist group;
    source-sha duplicates share a group; unknowns are descriptive only."""
    ids = sorted(set(spectra) | set(annotations) | set(artists))
    parents = {rid: rid for rid in ids}
    def find(rid):
        while parents[rid] != rid:
            parents[rid] = parents[parents[rid]]; rid = parents[rid]
        return rid
    def join(a, b): parents[find(b)] = find(a)
    by_artist = {}; by_sha = {}
    artist_groups = {rid: group_for(rid, artists.get(rid, ('', ''))[0]) for rid in ids}
    for rid in ids:
        group = artist_groups[rid]
        if group is not None:
            if group in by_artist: join(rid, by_artist[group])
            else: by_artist[group] = rid
        if rid in spectra:
            sha = spectra[rid][1]
            if sha in by_sha: join(rid, by_sha[sha])
            else: by_sha[sha] = rid
    components = defaultdict(list)
    for rid in ids: components[find(rid)].append(rid)
    group_info = {}
    observed_groups = {normalize_artist(g) for g in observed_groups}
    for component in components.values():
        known = {artist_groups[r] for r in component if artist_groups[r] is not None}
        observed = bool(set(component) & observed_ids or known & observed_groups)
        unknown = any(artist_groups[r] is None for r in component)
        group = (next(iter(known)) if len(known) == 1 else
                 'connected:' + sha256_bytes('|'.join(component).encode())[:20]) if not unknown else None
        for rid in component: group_info[rid] = (group, observed, unknown)
    records, excluded = [], []
    for rid in ids:
        artist, title = artists.get(rid, ("", ""))
        group, observed_component, unknown = group_info[rid]
        rec = dict(id=rid, artist=artist, title=title, group=group,
                   spectrogram=spectra.get(rid), annotation=annotations.get(rid))
        if rid in observed_ids: excluded.append({**rec, "reason": "observed_id"}); continue
        if observed_component:
            reason = 'observed_artist_group' if artist_groups[rid] in observed_groups else 'observed_duplicate_or_artist_component'
            excluded.append({**rec, "reason": reason}); continue
        if rid not in spectra or rid not in annotations:
            rec['reason'] = 'missing_spectrogram_or_annotation'
        if unknown:
            rec.update(split="descriptive_only", reason="unknown_artist")
        records.append(rec)
    splits = assign_splits({r["group"] for r in records if r.get("split") is None and r["group"]})
    for r in records:
        if r.get("split") is None: r["split"] = splits[r["group"]]
    # Duplicate spectrograms must never cross splits: force minority to majority split.
    by_group = defaultdict(set)
    for r in records: by_group[r["group"]].add(r["split"])
    assert all(len(v) == 1 for v in by_group.values()), "group leakage across splits"
    return records, excluded

def runtime_identity(root):
    sys.path.insert(0, str(root))
    import studio_structure
    import beat_this.inference
    import beat_this.model.postprocessor
    paths = [root / n for n in ('studio_structure.py', 'studio_beat_tempo.py', 'studio_structure_features.py')]
    paths += [Path(beat_this.inference.__file__), Path(beat_this.model.postprocessor.__file__)]
    model = studio_structure.model_path()
    if sha256_file(model) != studio_structure.MODEL_SHA256:
        raise ValueError('Pinned timing model is missing or damaged.')
    return {'source_sha256': {str(p): sha256_file(p) for p in paths},
            'model_sha256': studio_structure.MODEL_SHA256, 'model_path': str(model),
            'runtime': {n: metadata.version(n) for n in ('torch', 'beat-this', 'numpy', 'scipy')}}


def verify_archive(path, expected_size, expected_md5):
    if Path(path).stat().st_size != expected_size:
        raise ValueError('Official archive byte count differs: ' + str(path))
    h = hashlib.md5()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''): h.update(block)
    if h.hexdigest() != expected_md5:
        raise ValueError('Official archive checksum differs: ' + str(path))


def cmd_prepare(args):
    root, out = Path(args.project_root).resolve(), Path(args.out).resolve()
    spec_p, ann_p = Path(args.spectrograms).resolve(), Path(args.annotations).resolve()
    for p in (spec_p, ann_p):
        if not p.is_file(): raise SystemExit(f"missing archive: {p}")
    verify_archive(spec_p, 306944985, '39a7dfe6a6b0a5279a94d770506db879')
    verify_archive(ann_p, 7241621, 'a11e22e5f9aec7b14d8dd92b6251117c')
    prior = json.loads((root / "outputs/gtzan-timing-confidence/plan.json").read_text())
    observed_ids = {r["id"] for r in prior["records"]}
    observed_groups = {normalize_artist(r["group"]) for r in prior["records"]} - {""}
    artists = parse_artist_index((root / "outputs/gtzan-timing-feasibility/artist-index.txt").read_text())
    out.mkdir(parents=True, exist_ok=True)
    npz_path = out / 'source-gtzan.npz'
    with zipfile.ZipFile(spec_p) as zf:
        if zf.namelist() != ['gtzan.npz']:
            raise ValueError('Unexpected official spectrogram archive layout.')
        expected_inner = _member_sha(zf, zf.getinfo('gtzan.npz'))
        if npz_path.exists():
            if sha256_file(npz_path) != expected_inner:
                raise ValueError('Extracted spectrogram source changed.')
        else:
            pending = npz_path.with_suffix('.pending')
            try:
                with zf.open('gtzan.npz') as source, pending.open('xb') as target:
                    for block in iter(lambda: source.read(1 << 20), b''): target.write(block)
                if sha256_file(pending) != expected_inner: raise ValueError('Incomplete spectrogram extraction.')
                pending.replace(npz_path)
            finally: pending.unlink(missing_ok=True)
    with zipfile.ZipFile(npz_path) as zf: spectra = discover_spectra(zf)
    annotations = {}
    with zipfile.ZipFile(ann_p) as zf:
        for info in _safe_members(zf):
            if info.filename.startswith(ANN_PREFIX) and info.filename.endswith(".beats"):
                rid = recording_id(info.filename)
                if rid:
                    if rid in annotations or info.file_size > 100_000:
                        raise ValueError('Duplicate or oversized annotation: ' + info.filename)
                    annotations[rid] = (info.filename, _member_sha(zf, info))
    records, excluded = build_records(spectra, annotations, artists, observed_ids, observed_groups)
    identity = dict(spectrograms_sha256=sha256_file(spec_p), annotations_sha256=sha256_file(ann_p),
                    runner_sha256=sha256_file(Path(__file__).resolve()),
                    prior_plan_sha256=sha256_file(root / "outputs/gtzan-timing-confidence/plan.json"),
                    artist_index_sha256=sha256_file(root / "outputs/gtzan-timing-feasibility/artist-index.txt"),
                    **runtime_identity(root), source_npz_sha256=expected_inner,
                    seed=SEED, source_paths=dict(project_root=str(root), spectrograms=str(spec_p),
                                                source_npz=str(npz_path), annotations=str(ann_p)))
    plan = dict(schema=1, research_only=True, ui_promotion_allowed=False, identity=identity,
                records=records, excluded=excluded,
                counts=dict(planned=len(records), excluded=len(excluded), spectra=len(spectra), annotations=len(annotations)),
                limitations=['Official precomputed features use float16; frontend equivalence must be checked separately.',
                             'Previously observed artists and duplicate-connected components are excluded.',
                             'Unknown artists are descriptive only; no calibrated UI confidence is authorized.'])
    plan['plan_sha256'] = digest(plan)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "plan.json"
    if target.exists():
        if json.loads(target.read_text()).get("identity") != identity: raise SystemExit("plan identity changed; use new --out")
        print(json.dumps(dict(status="already_prepared", **plan["counts"]))); return
    with target.open("x") as f: json.dump(plan, f, indent=2, allow_nan=False); f.write("\n")
    print(json.dumps(dict(status="prepared", **plan["counts"])))

# ---------------- run ----------------

def load_spect(zf, member):
    if zf.getinfo(member).file_size > 4_000_000:
        raise ValueError('Spectrogram exceeds the bounded recording size.')
    if "::" in member:
        name, key = member.rsplit("::", 1)
        with zf.open(name) as f: raw = f.read()
        with np.load(_BytesIO(raw), allow_pickle=False) as d: arr = np.asarray(d[key])
    else:
        with zf.open(member) as f: raw = f.read()
        arr = np.load(_BytesIO(raw), allow_pickle=False)
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != MEL or not np.isfinite(arr).all():
        raise ValueError(f"spectrogram must be (T,{MEL}) finite, got {arr.shape}")
    return arr

def default_predict(spect, model_path):
    """Identical production path: Audio2Frames.spect2frames + Postprocessor."""
    import torch
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import studio_structure
    if sha256_file(model_path) != studio_structure.MODEL_SHA256: raise ValueError("model hash mismatch")
    from beat_this.inference import Audio2Frames
    from beat_this.model.postprocessor import Postprocessor
    if not hasattr(default_predict, '_runner'):
        default_predict._runner = Audio2Frames(str(model_path), device="cpu")
        default_predict._model_sha = studio_structure.MODEL_SHA256
    if default_predict._model_sha != studio_structure.MODEL_SHA256:
        raise ValueError('Timing model changed within the research process.')
    runner = default_predict._runner
    with torch.inference_mode():
        beat_logits, downbeat_logits = runner.spect2frames(torch.from_numpy(np.ascontiguousarray(spect)))
        beats, downbeats = Postprocessor()(beat_logits, downbeat_logits)
    return (np.asarray(beats, float), np.asarray(downbeats, float),
            beat_logits.numpy(), downbeat_logits.numpy(), studio_structure)

def run_one(rec, spec_zf, ann_zf, predict, studio):
    duration = None
    try:
        if not rec.get('spectrogram') or not rec.get('annotation'):
            raise ValueError('Missing official spectrogram or annotation; attempted record retained.')
        for archive, entry in ((spec_zf, rec['spectrogram']), (ann_zf, rec['annotation'])):
            if _member_sha(archive, archive.getinfo(entry[0])) != entry[1]:
                raise ValueError('Reference member hash changed.')
        spect = load_spect(spec_zf, rec["spectrogram"][0])
        duration = len(spect) / FPS
        raw = ann_zf.read(rec["annotation"][0])
        rows = parse_beats(raw, duration)
        beats, downbeats, bl, dl, _ = predict(spect)
        keep = beats < duration; beats = beats[keep]
        keep = downbeats < duration; downbeats = downbeats[keep]
        bs = studio._peak_values(bl, beats); ds = studio._peak_values(dl, downbeats)
        from studio_beat_tempo import tempo_from_beats
        tempo = tempo_from_beats(beats, duration)
        grid = (studio.coherent_grid(beats, downbeats, bs, ds, tempo["bpm"], duration)
                if tempo["bpm"] else dict(beats=[], downbeats=[], state="uncertain", coverage=0.))
        ref = dict(beats=[t for t, _ in rows], downbeats=[t for t, b in rows if b == 1])
        four = is_four_beat(rows)
        scores = {}
        for kind, pred in (("raw", dict(beats=beats, downbeats=downbeats)),
                           ("coherent_grid", dict(beats=grid["beats"], downbeats=grid["downbeats"]))):
            scores[kind] = {ev: {str(t): score_events(ref[ev], pred[ev], t) for t in TOLS} for ev in ref}
        outcomes = {}
        for ev, est, strengths in (("beats", beats, bs), ("downbeats", downbeats, ds)):
            pairs, _, _ = match_events(ref[ev], est, 0.07)
            hit = {j for _, j in pairs}
            outcomes[ev] = dict(times=np.asarray(est).tolist(), scores=np.asarray(strengths).tolist(),
                                correct_70ms=[int(j in hit) for j in range(len(est))])
        return dict(id=rec["id"], split=rec["split"], group=rec["group"], status="scored",
                    four_beat_compatible=four, duration=duration, scores=scores, outcomes=outcomes,
                    duration_scope='spectrogram frame count / 50; true audio endpoint may differ by one frame',
                    grid_state=grid.get("state"), grid_coverage=grid.get("coverage"))
    except Exception as e:
        return dict(id=rec["id"], split=rec["split"], group=rec["group"], status="failed",
                    error=f"{type(e).__name__}: {e}", duration=duration)

def pooled(rows):
    agg = {}
    for r in rows:
        if r["status"] != "scored": continue
        for kind in r["scores"]:
            for ev in r["scores"][kind]:
                for tol, s in r["scores"][kind][ev].items():
                    k = agg.setdefault((r["split"], kind, ev, tol), dict(matched=0, fp=0, fn=0, ref=0, pred=0))
                    k["matched"] += s["matched"]; k["fp"] += s["false_positive"]; k["fn"] += s["missed"]
                    k["ref"] += s["reference_count"]; k["pred"] += s["predicted_count"]
    return {"/".join(k): dict(v, precision=v["matched"] / v["pred"] if v["pred"] else 0.,
                              recall=v["matched"] / v["ref"] if v["ref"] else 0.,
                              f1=2*v['matched']/(v['ref']+v['pred']) if v['ref']+v['pred'] else 0.) for k, v in agg.items()}

def cmd_run(args):
    out = Path(args.out).resolve()
    plan = json.loads((out / "plan.json").read_text())
    if plan.get('plan_sha256') != digest({k:v for k,v in plan.items() if k != 'plan_sha256'}):
        raise ValueError('Frozen reference plan changed.')
    ident = plan["identity"]
    root = Path(ident["source_paths"]["project_root"])
    spec_p, ann_p = Path(ident["source_paths"]["spectrograms"]), Path(ident["source_paths"]["annotations"])
    if sha256_file(spec_p) != ident["spectrograms_sha256"] or sha256_file(ann_p) != ident["annotations_sha256"]:
        raise SystemExit("archive identity changed; re-prepare in new --out")
    if sha256_file(Path(__file__).resolve()) != ident["runner_sha256"]: raise SystemExit("runner changed; re-prepare")
    if runtime_identity(root) != {k: ident[k] for k in ('source_sha256', 'model_sha256', 'model_path', 'runtime')}:
        raise ValueError('Frozen detector source, model or runtime changed.')
    if sha256_file(ident['source_paths']['source_npz']) != ident['source_npz_sha256']:
        raise ValueError('Extracted reference source changed.')
    sys.path.insert(0, str(root))
    import studio_structure
    model = studio_structure.model_path()
    model_sha = sha256_file(model) if model.is_file() else None
    if model_sha != studio_structure.MODEL_SHA256: raise SystemExit(f"pinned model missing/mismatch: {model}")
    records = plan["records"]
    diagnostic = args.limit is not None
    if diagnostic: records = records[:args.limit]
    res_dir = out / "results"; res_dir.mkdir(exist_ok=True)
    predict = lambda s: default_predict(s, model)
    rows = []
    execution_sha = digest({'plan':plan['plan_sha256'], 'identity': ident})
    import torch
    threads = torch.get_num_threads(); torch.set_num_threads(4)
    try:
        with zipfile.ZipFile(ident['source_paths']['source_npz']) as sz, zipfile.ZipFile(ann_p) as az:
            for index, rec in enumerate(records):
                f = res_dir / (rec["id"] + ".json")
                if f.exists():
                    old = json.loads(f.read_text())
                    if old.get('identity_sha') != execution_sha or old.get('record_sha256') != digest(rec):
                        raise ValueError('Saved reference row differs from this frozen execution.')
                    rows.append(old); continue
                row = run_one(rec, sz, az, predict, studio_structure)
                row.update(identity_sha=execution_sha, record_sha256=digest(rec), model_sha256=model_sha)
                atomic_json(f, row); rows.append(row)
                print(f"Reference {index+1}/{len(records)} {rec['id']}: {row['status']}", flush=True)
                if (index+1) % 10 == 0:
                    atomic_json(out/'progress.json', {'attempted':len(rows), 'planned':len(records),
                                                    'failed':sum(r['status']=='failed' for r in rows)})
    finally:
        torch.set_num_threads(threads)
        if hasattr(default_predict, '_runner'): del default_predict._runner
    if runtime_identity(root) != {k: ident[k] for k in ('source_sha256', 'model_sha256', 'model_path', 'runtime')}:
        raise ValueError('Detector implementation changed during reference inference.')
    four = [r for r in rows if r.get("four_beat_compatible")]
    summary = dict(status="diagnostic" if diagnostic else
                   "complete_with_failures" if any(r['status']=='failed' for r in rows) else "complete",
                   completion_claim=not diagnostic, attempted=len(rows),
                   plan_sha256=plan['plan_sha256'], ui_promotion_allowed=False,
                   metric_scope='Precision/recall conditional on successfully scored valid references; failed records retained in attempted counts.',
                   scored=sum(r["status"] == "scored" for r in rows),
                   failed=sum(r["status"] == "failed" for r in rows),
                   pooled=pooled(rows), pooled_four_beat=pooled(four),
                   abstentions=[r["id"] for r in rows if r.get("grid_state") in ("uncertain", None) and r["status"] == "scored"])
    atomic_json(out/'summary.json', summary)
    print(json.dumps({k: summary[k] for k in ("status", "attempted", "scored", "failed")}))

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", required=True); p.add_argument("--spectrograms", required=True)
    p.add_argument("--annotations", required=True); p.add_argument("--out", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    r = sub.add_parser("run"); r.add_argument("--limit", type=int, default=None, help="diagnostic only")
    a = p.parse_args(argv)
    if getattr(a, 'limit', None) is not None and a.limit < 1: p.error('--limit must be positive')
    (cmd_prepare if a.cmd == "prepare" else cmd_run)(a)

if __name__ == "__main__": main()
