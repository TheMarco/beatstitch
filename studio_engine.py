"""Project model and offline multitrack rendering for the desktop application."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from contextlib import ExitStack
import hashlib
import json
import math
import os
import re
from pathlib import Path
import tempfile
from typing import Callable
import uuid

import numpy as np

import djmix
from studio_storage import managed_cache


@dataclass
class Track:
    id: str
    path: str
    name: str
    duration: float
    bpm: float | None = None
    key: str = ""
    cue_in: float = 0
    cue_out: float = 0
    estimated: bool = True
    waveform: list[float] = field(default_factory=list)
    status: str = "Pending analysis"
    timing: dict = field(default_factory=dict)
    structure: dict = field(default_factory=dict)
    beat_grid: dict = field(default_factory=dict)
    fingerprint: dict = field(default_factory=dict)
    content: dict = field(default_factory=dict)
    song_sections: dict = field(default_factory=dict)
    key_origin: str = 'saved'
    key_analysis: dict = field(default_factory=dict)
    opening_tempo: dict = field(default_factory=dict)


@dataclass
class Clip:
    track_id: str
    cue_in: float
    cue_out: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    blend_bars: int = 8
    transpose: bool = False
    semitones: int = 0
    bass_swap: bool = True
    intro_percussive: bool = False
    outro_percussive: bool = False
    stabilize: bool = False
    order_locked: bool = False
    energy: int | None = None
    protected_moments: list[dict] = field(default_factory=list)
    balance_levels: bool = False
    transition_effect: str = 'none'
    fades_linked: bool = True
    incoming_fade_bars: float | None = None
    outgoing_fade_bars: float | None = None
    incoming_fade_curve: str = 'smooth'
    outgoing_fade_curve: str = 'smooth'

    @property
    def shift(self):
        return self.semitones if self.transpose else 0


@dataclass
class Session:
    name: str = "Untitled mix"
    target_bpm: float = 128
    tracks: dict[str, Track] = field(default_factory=dict)
    clips: list[Clip] = field(default_factory=list)
    planning: dict = field(default_factory=dict)
    collections: dict[str, list[str]] = field(default_factory=dict)
    fade_in_bars: int = 0
    fade_out_bars: int = 0
    tempo_enabled: bool = False
    tempo_points: list[dict] = field(default_factory=list)
    mastering_enabled: bool = False
    listening_notes: list[dict] = field(default_factory=list)

    def to_dict(self):
        return {"version": 17, **asdict(self)}

    @classmethod
    def from_dict(cls, value):
        if value.get("version") not in range(1, 18):
            raise djmix.MixError("Unsupported project version.")
        result = cls(name=str(value["name"]), target_bpm=float(value["target_bpm"]),
                     tracks={k: Track(**v) for k, v in value["tracks"].items()},
                     clips=[Clip(**v) for v in value["clips"]], planning=value.get('planning', {}),
                     collections=value.get('collections', {}), fade_in_bars=value.get('fade_in_bars', 0),
                     fade_out_bars=value.get('fade_out_bars', 0),
                     tempo_enabled=value.get('tempo_enabled', False), tempo_points=value.get('tempo_points', []),
                     mastering_enabled=value.get('mastering_enabled', False),
                     listening_notes=value.get('listening_notes', []))
        from studio_listening_notes import validate_notes
        validate_notes(result.listening_notes)
        if type(result.mastering_enabled) is not bool:
            raise djmix.MixError('Automatic mastering must be on or off.')
        if not isinstance(result.planning, dict):
            raise djmix.MixError('Invalid song-order preferences.')
        from studio_library import validate_collections
        validate_collections(result.collections, result.tracks)
        for track in result.tracks.values():
            if not isinstance(track.opening_tempo, dict):
                raise djmix.MixError('Invalid opening-passage tempo estimate.')
            if track.key_origin not in ('saved', 'manual', 'detected', 'pending') or not isinstance(track.key_analysis, dict):
                raise djmix.MixError('Invalid source-key settings.')
            if track.key:
                track.key = djmix.key_name(djmix.parse_key(track.key))
        # The project may intentionally contain missing files; rendering checks those.
        layout(result)
        return result

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)


def track_id(path: Path):
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]


def load_track(path: Path, cache: Path, *, verified=None, progress=lambda message: None) -> Track:
    from studio_source_identity import fingerprint, valid_fingerprint
    info = djmix.probe(path)
    identity = track_id(path)
    signature = [str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns]
    cached = cache / f"{identity}.json"
    if cached.is_file():
        try:
            data = json.loads(cached.read_text())
            if data["signature"] == signature:
                track = Track(**data['track'])
                if not valid_fingerprint(track.fingerprint) or track.fingerprint['source'] != signature:
                    track.fingerprint = verified or fingerprint(path, progress)
                return track
        except (ValueError, KeyError, TypeError):
            pass
    raw = djmix.ffmpeg("-i", str(path), "-map", "0:a:0", "-ar", "8000", "-ac", "1", "-f", "f32le", "-").stdout
    samples = np.abs(np.frombuffer(raw, dtype="<f4"))
    size = 1400
    step = max(1, math.ceil(len(samples) / size))
    padded = np.pad(samples, (0, (-len(samples)) % step))
    peaks = padded.reshape(-1, step).max(axis=1)
    norm = max(float(np.percentile(peaks, 98)), 0.001)
    name = re.sub(r"^\d+[-_ ]+", "", path.stem).replace("_", " ").replace("-", " ")
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    track = Track(identity, str(path.resolve()), name, info["duration"], cue_out=info["duration"],
                  waveform=np.clip(peaks / norm, 0, 1).round(4).tolist(), fingerprint=verified or fingerprint(path, progress),
                  key_origin='pending')
    from studio_cache import source_signature
    if track.fingerprint['source'] != signature or source_signature(path) != signature:
        raise djmix.MixError(f'{path.name} changed during import. Import it again.')
    return track


def cache_track(track: Track, cache: Path):
    path = Path(track.path)
    cache.mkdir(parents=True, exist_ok=True)
    data = {"signature": [str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns], "track": asdict(track)}
    (cache / f"{track.id}.json").write_text(json.dumps(data, allow_nan=False))


def estimate_track(track: Track, timing_cache=None, progress=lambda message: None) -> Track:
    from analyze_audio import analyze
    from studio_key import analyze_key, remember_analysis
    key_cache = Path(timing_cache).parent / 'library-cache' / 'keys' if timing_cache is not None else None
    try:
        result = analyze_key(Path(track.path), key_cache, progress)
        remember_analysis(track, result, update_automatic=True)
    except Exception as error:
        if str(error) == 'Operation cancelled.':
            raise
        # A missing detector must not erase a chosen key or prevent BPM analysis.
        track.key_analysis = {'state': 'unavailable', 'reason': str(error)[:1200]}
    from studio_cache import source_signature
    from studio_structure import analyze_tempo, analyze_song
    from studio_timing import analyze as analyze_timing
    from studio_process import check_cancelled
    signature = source_signature(track.path)
    chosen, opening_error = None, None
    try:
        duration = min(60.0, track.duration)
        result = analyze(Path(track.path), 0, duration, bars=8 if duration >= 25 else 4)
        windows = result["candidate_windows"]
        stable = [w for w in windows if w["grid_residual_p95_ms"] < 25] or windows
        if stable:
            chosen = min(stable[:8], key=lambda w: w["grid_residual_p95_ms"])
    except Exception as error:
        if str(error) == 'Operation cancelled.':
            raise
        opening_error = str(error)
    with ExitStack() as stack:
        structure_cache = (Path(timing_cache).parent / 'structure-cache' if timing_cache is not None else
                           Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='dj-tempo-import-'))))
        estimate, beat_error = {}, None
        try:
            progress('Checking tempo against detected beats')
            estimate = analyze_tempo(track.path, track.duration, structure_cache, progress)
        except Exception as error:
            if str(error) == 'Operation cancelled.':
                raise
            beat_error = str(error)
        initial = estimate.get('bpm')
        beat_based = initial is not None
        if initial is None:
            if chosen is None:
                detail = beat_error or opening_error or 'No consistent beat passages found.'
                raise djmix.MixError('No usable tempo estimate. Enter BPM manually. ' + detail[:300])
            initial = chosen['bpm']
            progress('Beat estimate unavailable; using the opening-passage estimate')
        timing = analyze_timing(Path(track.path), initial, track.duration, timing_cache, progress)
        bpm = timing['bpm'] if timing['bpm'] is not None else initial
        structure = {}
        if not beat_error:
            try:
                structure = analyze_song(track.path, bpm, track.duration, structure_cache, progress)
            except Exception as error:
                if str(error) == 'Operation cancelled.':
                    raise
                progress('Tempo estimated; musical cues can be checked later')
        check_cancelled()
        if source_signature(track.path) != signature:
            raise djmix.MixError('The song changed during analysis. Import it again.')
        cue_in = chosen['cue_seconds'] if chosen else 0.
        bar = 240 / bpm
        cue_out = min(track.duration, cue_in + math.floor((track.duration - cue_in) / bar) * bar)
        # Very short songs still need a usable clip, even without a whole bar.
        if cue_out <= cue_in:
            cue_out = track.duration
        track.bpm, track.timing, track.structure = bpm, timing, structure
        track.opening_tempo = ({'version': 1, 'source': signature, 'duration': track.duration,
                                'bpm': chosen['bpm']} if chosen else {})
        track.cue_in, track.cue_out = cue_in, cue_out
        track.status = ('Beat-based tempo estimate · review beats and cues' if beat_based else
                        'Opening-passage tempo estimate · check BPM and cues')
    return track


def layout(session: Session, *, check_protection=True) -> list[dict]:
    if type(session.mastering_enabled) is not bool:
        raise djmix.MixError('Automatic mastering must be on or off.')
    from studio_tempo_curve import session_curve
    session_curve(session)
    if not math.isfinite(session.target_bpm) or not 20 <= session.target_bpm <= 300:
        raise djmix.MixError("Set a project tempo between 20 and 300 BPM.")
    result = []
    ids = set()
    for index, clip in enumerate(session.clips):
        if clip.id in ids:
            raise djmix.MixError("The project contains duplicate clip identities.")
        ids.add(clip.id)
        track = session.tracks.get(clip.track_id)
        if track is None or track.bpm is None or not math.isfinite(track.bpm) or not 20 <= track.bpm <= 300:
            raise djmix.MixError("Analyze the song or enter its BPM before adding it to the arrangement.")
        if not all(math.isfinite(v) for v in (track.duration, clip.cue_in, clip.cue_out)):
            raise djmix.MixError("Track and cue durations must be finite.")
        if not 0 <= clip.cue_in < clip.cue_out <= track.duration + 1 / djmix.SR:
            raise djmix.MixError(f"Invalid in/out points for {track.name}.")
        if not isinstance(clip.blend_bars, int) or not 1 <= clip.blend_bars <= 32:
            raise djmix.MixError("Blend length must be between 1 and 32 bars.")
        from studio_fades import validate as validate_fades
        validate_fades(clip)
        if not isinstance(clip.semitones, int) or not -12 <= clip.semitones <= 12:
            raise djmix.MixError("Pitch adjustment must be between -12 and +12 semitones.")
        if not isinstance(clip.balance_levels, bool):
            raise djmix.MixError('Blend level balance must be on or off.')
        if clip.transition_effect not in ('none', 'filter', 'echo'):
            raise djmix.MixError('Choose No extra effect, Filter out, or Echo out for the transition.')
        if not isinstance(clip.order_locked, bool) or (clip.energy is not None and
                (type(clip.energy) is not int or not 1 <= clip.energy <= 5)):
            raise djmix.MixError('Song energy must be unrated or 1–5, and position locks must be on or off.')
        from studio_timing import corrected_time
        length = round((corrected_time(track, clip, clip.cue_out) - corrected_time(track, clip, clip.cue_in))
                       * track.bpm / session.target_bpm * djmix.SR)
        overlap = round(clip.blend_bars * 240 / session.target_bpm * djmix.SR) if index else 0
        if index and (overlap >= length or overlap + result[-1]["incoming"] >= result[-1]["length"]):
            raise djmix.MixError("A blend is longer than the available audio. Shorten it or widen the clip's in/out points.")
        start = result[-1]["end"] - overlap if index else 0
        result.append({"clip_id": clip.id, "start": start, "end": start + length,
                       "length": length, "incoming": overlap})
    from studio_protection import validate_moments, validate_protection
    for clip in session.clips:
        validate_moments(session.tracks[clip.track_id], clip)
    if check_protection:
        validate_protection(session, result)
    from studio_finishing import validate as validate_finishing
    validate_finishing(session, result, check_protection=check_protection)
    return result


def preview_range(session: Session, index: int):
    positions = layout(session)
    if index < 1 or index >= len(positions):
        raise djmix.MixError("Select an incoming track to preview its blend.")
    position = positions[index]
    context = round(4 * 240 / session.target_bpm * djmix.SR)
    return max(0, position["start"] - context), min(positions[-1]["end"], position["start"] + position["incoming"] + context)


def overlap_limit(session: Session, index: int, positions=None):
    """Largest whole-bar overlap that leaves room for both adjacent transitions.

    Use the same rounded sample lengths and strict inequality as layout. The
    input arrangement must already be valid; this function never changes cues.
    """
    positions = layout(session) if positions is None else positions
    if not 0 < index < len(positions):
        raise djmix.MixError("Select a transition between two songs.")
    outgoing, incoming = positions[index - 1], positions[index]
    next_overlap = positions[index + 1]["incoming"] if index + 1 < len(positions) else 0
    available = (outgoing["length"] - outgoing["incoming"], incoming["length"] - next_overlap)
    side = 0 if available[0] <= available[1] else 1
    limit = min(available)
    from studio_protection import protection_limits, BOUNDARY_TOLERANCE
    protected = []
    for offset, direction in ((0, 'outgoing'), (1, 'incoming')):
        clip = session.clips[index - 1 + offset]
        constraint = protection_limits(session.tracks[clip.track_id], clip, session.target_bpm)[direction]
        if constraint is not None:
            protected.append((constraint[0], offset, constraint[1]))
    protection = min(protected) if protected else None
    bars = max((n for n in range(1, 33)
                if round(n * 240 / session.target_bpm * djmix.SR) < limit
                and (protection is None or round(n * 240 / session.target_bpm * djmix.SR) <= protection[0] + BOUNDARY_TOLERANCE)), default=0)
    constrained = protection is not None and protection[0] < limit - 1
    if constrained: side = protection[1]
    track = session.tracks[session.clips[index - 1 + side].track_id]
    return {"bars": bars, "seconds": bars * 240 / session.target_bpm,
            "available_seconds": limit / djmix.SR, "song": track.name,
            "neighbor": bool(outgoing["incoming"] if side == 0 else next_overlap),
            "protected": protection[2] if constrained else None}


def blend_advice(session: Session, outgoing: Clip, incoming: Clip):
    return djmix.harmonic_advice(session.tracks[outgoing.track_id].key,
                                session.tracks[incoming.track_id].key,
                                outgoing.shift, incoming.shift,
                                "percussive" if outgoing.outro_percussive else "musical",
                                "percussive" if incoming.intro_percussive else "musical")


def suggest_order(session: Session) -> list[Clip]:
    """Keep the opening song; greedily prefer compatible keys and nearby source BPMs.

    A proposal only: no mutation, cue changes, or automatic transposition.
    """
    if len(session.clips) >= 3 and any(clip.protected_moments for clip in session.clips):
        from studio_order import propose_order, OrderOptions
        return propose_order(session, OrderOptions(opener=session.clips[0].id, auto_energy=False))['draft'].clips
    if len(session.clips) < 3:
        return list(session.clips)
    ordered, remaining = [session.clips[0]], list(session.clips[1:])
    while remaining:
        previous = ordered[-1]
        a = session.tracks[previous.track_id]
        def score(clip):
            b = session.tracks[clip.track_id]
            ka = djmix.transpose(djmix.parse_key(a.key), previous.shift)
            kb = djmix.transpose(djmix.parse_key(b.key), clip.shift)
            compatible = previous.outro_percussive or clip.intro_percussive or (ka and kb and djmix.relationship(ka, kb))
            penalty = 0 if compatible else (1 if not ka or not kb else 2)
            return penalty, abs(math.log2(b.bpm / a.bpm))
        chosen = min(remaining, key=score)
        ordered.append(chosen); remaining.remove(chosen)
    return ordered


def _weights(n: np.ndarray, start: int, length: int, incoming: bool, bass_swap: bool, target: float, clip=None):
    progress = np.clip((n - start) / max(1, length - 1), 0, 1)
    high = np.sin(progress * np.pi / 2) if incoming else np.cos(progress * np.pi / 2)
    if clip is not None:
        from studio_fades import gain
        high = gain(progress, incoming, clip)
    if not bass_swap:
        return high, high
    width = min(length, round(240 / target * djmix.SR))
    p = np.clip((n - start - (length - width) / 2) / max(1, width - 1), 0, 1)
    low = (1 - np.cos(p * np.pi)) / 2
    return (low if incoming else 1 - low), high


@managed_cache
def render_session(session: Session, destination: Path, *, region=None,
                   progress: Callable[[str], None] = lambda message: None, cache_dir: Path | None = None,
                   stems: dict[int, Path] | None = None) -> dict:
    """Assemble sources before final mastering, retaining optional deck stems.

    Audition stems include the same transition envelopes and share headroom with
    the mix. They are never independently normalized: soloing preserves balance.
    Callers needing atomic publication of a set should use a staging directory.
    """
    djmix.require_tools()
    positions = layout(session)
    if not positions:
        raise djmix.MixError("Add songs to the timeline first.")
    start, end = region if region else (0, positions[-1]["end"])
    if not 0 <= start < end <= positions[-1]["end"]:
        raise djmix.MixError("Invalid preview range.")
    from studio_tempo_curve import session_curve, output_positions, tempo_token
    curve = session_curve(session)
    reference_positions, reference_start = positions, start
    start, end = curve.output_span(start, end)
    positions = output_positions(curve, reference_positions)
    tempo = tempo_token(session)
    stems = dict(stems or {})
    if any(not isinstance(index, int) or not 0 <= index < len(positions) for index in stems):
        raise djmix.MixError("Invalid song selected for audition.")
    destinations = [destination, *stems.values()]
    resolved = [path.resolve() for path in destinations]
    if len(set(resolved)) != len(resolved):
        raise djmix.MixError("Each audition file needs a different filename.")
    sources = [Path(t.path) for t in session.tracks.values()]
    if any(path.resolve() == source.resolve() or
           (path.exists() and source.exists() and path.samefile(source))
           for path in destinations for source in sources):
        raise djmix.MixError("Choose an export filename different from your source songs.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    from studio_storage import require_space
    scratch_bytes = (end - start) * (22 + 24 * len(stems))
    require_space(destination.parent, scratch_bytes, 'prepare this mix')
    from studio_cache import AudioCache
    cache = AudioCache(cache_dir) if cache_dir else None
    reused, processed = 0, 0
    reused_mix_frames, mixed_mix_frames = 0, 0
    from studio_level_balance import VERSION as LEVEL_VERSION, active_overlaps, plan as level_plan, envelope as level_envelope, recipe as level_recipe
    overlaps = active_overlaps(session, positions, start, end)
    from studio_transition_effects import active_overlaps as effect_overlaps, plan as effect_plan, token as effect_token
    effect_spans = effect_overlaps(session, positions, start, end)
    selections = {i: [max(start, p['start']), min(end, p['end'])]
                  for i, p in enumerate(positions) if max(start, p['start']) < min(end, p['end'])}
    for index, a, b in overlaps:
        for i in (index - 1, index):
            selections[i] = [min(selections[i][0], a), max(selections[i][1], b)]
    for index, a, b in effect_spans:
        i = index - 1
        selections[i] = [min(selections[i][0], a), max(selections[i][1], b)]
    # Stage on the destination volume so final replacement is atomic.
    with tempfile.TemporaryDirectory(prefix="dj-render-", dir=destination.parent) as directory:
        # NumPy resolves memmap filenames, including Windows 8.3 aliases.
        # Keep scratch paths in that same form so cleanup retains live buffers.
        work = Path(directory).resolve()
        prepared, arrays, families = [], [], {}
        try:
            for index, (clip, position) in enumerate(zip(session.clips, positions)):
                if index not in selections:
                    continue
                left, right = selections[index]
                track = session.tracks[clip.track_id]
                progress(f"Checking {track.name} ({index + 1}/{len(session.clips)})")
                actual = djmix.probe(Path(track.path))
                if abs(actual["duration"] - track.duration) > 0.1:
                    raise djmix.MixError(f"{track.name} changed on disk. Re-import it before rendering.")
                ratio = track.bpm / session.target_bpm
                selection = {"start": clip.cue_in + (left - position["start"]) / djmix.SR / ratio,
                             "end": clip.cue_in + (right - position["start"]) / djmix.SR / ratio,
                             "frames": right - left}
                measurement = cache.measurement(Path(track.path)) if cache else djmix.meter(Path(track.path))
                gain = min(12, -18 - measurement["lufs"])
                local_start, local_end = left-position["start"], right-position["start"]
                from studio_timing import mapping, prepare as prepare_timing, signature
                warped = mapping(track, clip) is not None or not curve.identity
                if clip.stabilize and track.timing.get('source') != signature(track.path):
                    raise djmix.MixError(f"{track.name} changed on disk. Analyze its timing again.")
                from studio_tempo_processing import cache_token as source_tempo_token
                tempo_family = source_tempo_token(track, clip, curve, reference_positions[index]['start'] / djmix.SR) if tempo else None
                family = cache.family(track, clip, session.target_bpm, gain, tempo_family) if cache else None
                families[index] = family.name if family else None
                cached = cache.get(family, local_start, local_end) if cache else None
                if cached:
                    progress(f"Reusing cached audio · {track.name}")
                    low, high = djmix.raw_audio(cached[0]), djmix.raw_audio(cached[1])
                    cache_offset = cached[2]; reused += 1
                else:
                    detail = f'{track.bpm:g} → {session.target_bpm:g} BPM'
                    detail += f', pitch {clip.shift:+d} st' if clip.shift else ', original pitch'
                    progress(f"Preparing uncached audio · {track.name} ({index + 1}/{len(session.clips)}; {detail})")
                    duration = clip.cue_out - clip.cue_in if warped else selection['end'] - selection['start']
                    transform_bytes = 80 * max(position['length'] if warped else selection['frames'], round((duration + 4) * djmix.SR))
                    require_space(work, scratch_bytes + transform_bytes, f'process {track.name}')
                    if not curve.identity:
                        from studio_tempo_processing import prepare as prepare_tempo
                        progress(f"Applying the set tempo curve · {track.name}")
                        raw = prepare_tempo(track, clip, curve, reference_positions[index]['start'] / djmix.SR,
                                            work, str(index), gain)
                    elif warped:
                        progress(f"Stabilizing {track.name} · preparing the clip for reuse")
                        raw = prepare_timing(track, clip, session.target_bpm, work, str(index), gain)
                    else:
                        raw = djmix.prepare(Path(track.path), selection, track.duration, track.bpm,
                                            session.target_bpm, clip.shift, True, work, str(index), gain)
                    low, high = djmix.split_bands(raw, work, str(index))
                    cache_offset = local_start if warped else 0; processed += 1
                arrays.extend([low, high])
                if not cached:
                    if cache:
                        published = cache.put(family, 0 if warped else local_start,
                                              position['length'] if warped else local_end, Path(low.filename), Path(high.filename))
                        old_low, old_high = Path(low.filename), Path(high.filename)
                        low._mmap.close(); high._mmap.close()
                        low, high = djmix.raw_audio(published / 'low.raw'), djmix.raw_audio(published / 'high.raw')
                        arrays[-2:] = [low, high]
                        old_low.unlink(); old_high.unlink()
                    keep = {Path(low.filename), Path(high.filename)}
                    for temporary in [raw, *work.glob(f'{index}-*')]:
                        if temporary not in keep:
                            temporary.unlink(missing_ok=True)
                prepared.append((index, left, right, low, high, cache_offset))
            level_curves, level_reports = level_plan(session, positions, prepared, families, overlaps, work, cache, progress)
            effects, effect_report = effect_plan(session, positions, prepared, families, effect_spans, work, cache, progress)
            arrays.extend(effect['audio'] for effect in effects.values())
            for report in level_reports:
                report['relative_start'] = positions[report['index']]['start'] - start
            progress("Blending transitions")
            from studio_storage import RESERVE
            import shutil
            keep_assembly = bool(cache and shutil.disk_usage(work).free >= scratch_bytes + (end - start) * 16 + RESERVE)
            raw_mix = work / "arrangement.raw"
            raw_stems = {index: work / f"stem-{index}.raw" for index in stems}
            with ExitStack() as handles:
                handle = handles.enter_context(raw_mix.open("wb"))
                stem_handles = {index: handles.enter_context(path.open("wb")) for index, path in raw_stems.items()}
                boundaries = sorted({start, end, *(p[k] for p in positions for k in ('start', 'end') if start < p[k] < end)})
                pieces = ((offset, min(offset + djmix.BLOCK, b)) for a, b in zip(boundaries, boundaries[1:])
                          for offset in range(a, b, djmix.BLOCK))
                for block_index, (offset, stop) in enumerate(pieces):
                    if block_index % 64 == 0:
                        progress(f'Assembling audio · {(offset - start) / (end - start):.0%}')
                    n = np.arange(offset, stop)
                    active_sources = [entry for entry in prepared if entry[1] < stop and entry[2] > offset]
                    ingredients = []
                    for index, *_ in active_sources:
                        pos, clip = positions[index], session.clips[index]
                        incoming = ([offset - pos['start'], pos['incoming'], clip.bass_swap]
                                    if index and offset < pos['start'] + pos['incoming'] else None)
                        from studio_fades import token as fade_token
                        if incoming is not None and fade_token(clip): incoming.append(fade_token(clip))
                        outgoing = None
                        if index + 1 < len(positions) and stop > positions[index + 1]['start']:
                            nxt = positions[index + 1]
                            outgoing = [nxt['start'] - offset, nxt['incoming'], session.clips[index + 1].bass_swap]
                            if fade_token(session.clips[index + 1]): outgoing.append(fade_token(session.clips[index + 1]))
                            effect = effect_token(session.clips[index + 1])
                            if effect: outgoing.append(effect)
                        ingredient = [families[index], offset - pos['start'], incoming, outgoing]
                        balance = level_recipe(level_curves.get(index, []), offset, stop)
                        if balance:
                            ingredient.append(['level-balance', LEVEL_VERSION, balance])
                        ingredients.append(ingredient)
                    assembly_key = [session.target_bpm, ingredients]
                    if tempo:
                        a, b = curve.to_reference(np.array([offset, stop]) / djmix.SR)
                        assembly_key.append([curve.segment_token(a, b), offset])
                    cached_path, chunk = (cache.assembled(assembly_key, len(n))
                                          if cache and not stems else (None, None))
                    from_cache = chunk is not None
                    if from_cache:
                        reused_mix_frames += len(n)
                    else:
                        mixed_mix_frames += len(n)
                        chunk = np.zeros((len(n), 2), dtype=np.float64)
                    solos = {index: np.zeros_like(chunk) for index in stems}
                    for index, left, right, low, high, cache_offset in ([] if from_cache else active_sources):
                        mask = (n >= left) & (n < right)
                        if not mask.any():
                            continue
                        active = n[mask]
                        pos, clip = positions[index], session.clips[index]
                        musical = active if curve.identity else curve.to_reference(active / djmix.SR) * djmix.SR
                        ref_pos = reference_positions[index]
                        lo, hi = np.ones(len(active)), np.ones(len(active))
                        if index:
                            a, b = _weights(musical, ref_pos["start"], ref_pos["incoming"], True, clip.bass_swap, session.target_bpm, clip)
                            lo *= a; hi *= b
                        if index + 1 < len(positions):
                            nxt, next_clip = reference_positions[index + 1], session.clips[index + 1]
                            a, b = _weights(musical, nxt["start"], nxt["incoming"], False, next_clip.bass_swap, session.target_bpm, next_clip)
                            lo *= a; hi *= b
                        contribution = low[active-left+cache_offset] * lo[:, None] + high[active-left+cache_offset] * hi[:, None]
                        if index in effects:
                            effect = effects[index]
                            changed = (active >= effect['start']) & (active < effect['end'])
                            contribution[changed] = effect['audio'][active[changed] - effect['start']]
                        if index in level_curves:
                            contribution *= level_envelope(active, level_curves[index])[:, None]
                        chunk[mask] += contribution
                        if index in solos:
                            solos[index][mask] = contribution
                    if cached_path is not None and not from_cache and keep_assembly:
                        cache.put_assembled(cached_path, chunk)
                    edge = np.minimum(np.clip((n-start) / (0.005*djmix.SR), 0, 1),
                                      np.clip((end-1-n) / (0.005*djmix.SR), 0, 1))
                    from studio_finishing import envelope as finishing_envelope
                    musical = n if curve.identity else curve.to_reference(n / djmix.SR) * djmix.SR
                    edge *= finishing_envelope(session, reference_positions, musical)
                    chunk *= edge[:, None]
                    if not np.isfinite(chunk).all():
                        raise djmix.MixError("Invalid samples in rendered mix.")
                    handle.write(chunk.astype("<f4").tobytes())
                    for index, solo in solos.items():
                        solo *= edge[:, None]
                        if not np.isfinite(solo).all():
                            raise djmix.MixError("Invalid samples in an audition song.")
                        stem_handles[index].write(solo.astype("<f4").tobytes())
            progress("Checking export headroom")
            floating = work / "mix-float.wav"
            djmix.ffmpeg("-f", "f32le", "-ar", str(djmix.SR), "-ac", "2", "-i", str(raw_mix), "-c:a", "pcm_f32le", str(floating))
            measurement = djmix.meter(floating)
            attenuation = min(0, -1.1 - measurement["true_peak_db"])
            floating_stems = {}
            for index, raw in raw_stems.items():
                progress("Checking solo listening headroom")
                path = work / f"stem-{index}-float.wav"
                djmix.ffmpeg("-f", "f32le", "-ar", str(djmix.SR), "-ac", "2", "-i", str(raw), "-c:a", "pcm_f32le", str(path))
                attenuation = min(attenuation, -1.1 - djmix.meter(path, require_loudness=False)["true_peak_db"])
                floating_stems[index] = path
            final = work / "final.wav"
            djmix.export_wav(floating, final, attenuation)
            check = djmix.meter(final)
            actual_duration = djmix.probe(final)["duration"]
            if check["true_peak_db"] > -0.99 or abs(actual_duration - (end-start)/djmix.SR) > 2/djmix.SR:
                raise djmix.MixError("Export failed its duration or headroom check.")
            for index, floating_stem in floating_stems.items():
                path = work / f"stem-{index}-final.wav"
                djmix.ffmpeg("-i", str(floating_stem), "-af", f"volume={attenuation}dB", "-c:a", "pcm_f32le", str(path))
            progress("Finishing audio")
            for index, target in stems.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(work / f"stem-{index}-final.wav", target)
            os.replace(final, destination)
            return {"path": str(destination), "start": reference_start/djmix.SR, "duration": actual_duration,
                    "reference_end": reference_positions[-1]['end'] / djmix.SR if region is None else region[1] / djmix.SR,
                    "reused_tracks": reused, "processed_tracks": processed,
                    "reused_mix_frames": reused_mix_frames, "mixed_mix_frames": mixed_mix_frames,
                    "level_balance": level_reports,
                    **effect_report,
                    "attenuation_db": attenuation, **check}
        finally:
            # Explicit closure is necessary for Windows to remove mapped temporary files.
            for array in arrays:
                array._mmap.close()
            if cache:
                cache.prune()
