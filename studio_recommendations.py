"""Non-mutating tempo/key advice and separately qualified cached voice evidence."""
from __future__ import annotations

import math
from pathlib import Path

import djmix


def tempo_advice(session, preferred_percent=6.0):
    if session.tempo_enabled:
        return {'available': False, 'reason': 'The set tempo curve controls playback speed. Edit its points in Tempo curve, or switch it off for a fixed-tempo suggestion.'}
    rows = []
    for clip in session.clips:
        track = session.tracks[clip.track_id]
        bpm = track.bpm
        if bpm is None or not math.isfinite(bpm) or not 20 <= bpm <= 300:
            return {"available": False, "reason": f"Check the source BPM for {track.name}."}
        rows.append({"clip_id": clip.id, "name": track.name, "source": bpm,
                     "current_percent": (session.target_bpm / bpm - 1) * 100})
    if not rows:
        return {"available": False, "reason": "Add songs to the mix to get a tempo suggestion."}
    low, high = min(r["source"] for r in rows), max(r["source"] for r in rows)
    # Do not turn a possible half/double-time label into an extreme stretch.
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            ratio = max(a["source"], b["source"]) / min(a["source"], b["source"])
            if 1.92 <= ratio <= 2.08:
                return {"available": False, "rows": rows,
                        "reason": f'{a["name"]} and {b["name"]} have BPM labels about 2× apart. '
                                  "Check for half/double-time readings before choosing a shared tempo."}
    # t/low - 1 == 1 - t/high minimizes the largest absolute percentage.
    optimum = 2 * low * high / (low + high)
    candidates = {math.floor(optimum * 10) / 10, math.ceil(optimum * 10) / 10}
    worst = lambda target: max(abs(target / r["source"] - 1) * 100 for r in rows)
    target = min(candidates, key=lambda value: (worst(value), abs(value - session.target_bpm)))
    for row in rows:
        row["suggested_percent"] = (target / row["source"] - 1) * 100
    current_max, suggested_max = worst(session.target_bpm), worst(target)
    improves = current_max - suggested_max >= .1
    within = suggested_max <= preferred_percent + 1e-8
    return {"available": True, "target": target, "rows": rows,
            "current_max": current_max, "suggested_max": suggested_max,
            "improves": improves, "within_preference": within,
            "can_apply": improves and within,
            "reason": (f"Largest tempo change: {current_max:.1f}% now → {suggested_max:.1f}% at {target:g} BPM. "
                       + ("This balances the fastest and slowest songs." if within else
                          f"No shared tempo fits the preferred {preferred_percent:g}% limit. "
                          "Choose closer tempos, check the BPM labels, or adjust the preference."))}


def _key(value):
    try:
        return djmix.parse_key(value)
    except djmix.MixError:
        return None


def transition_harmony(session, index, shifts=None):
    outgoing, incoming = session.clips[index - 1:index + 1]
    a, b = session.tracks[outgoing.track_id], session.tracks[incoming.track_id]
    shifts = shifts or {}
    keys = [djmix.transpose(_key(track.key), shifts.get(clip.id, clip.shift))
            for track, clip in ((a, outgoing), (b, incoming))]
    if outgoing.outro_percussive or incoming.intro_percussive:
        state, reason = "percussion", "Marked as beats only; listen for pitched bass and tonal tails."
    elif None in keys:
        state, reason = "unknown", "Key advice needs both song key labels."
    else:
        relation = djmix.relationship(*keys)
        state = "compatible" if relation else "clash"
        reason = relation or "No simple match between the song key labels."
    return {"index": index, "state": state, "reason": reason,
            "outgoing": a.name, "incoming": b.name,
            "keys": [djmix.key_name(key) if key else "Unknown" for key in keys]}


def pitch_advice(session, index, max_shift=2):
    """Check both neighbors, with no new clash allowed on an already related pair."""
    if not 0 <= index < len(session.clips):
        return {"suggestion": None, "reason": "Select a song in the mix.", "neighbors": []}
    clip = session.clips[index]
    track = session.tracks[clip.track_id]
    adjacent = [i for i in (index, index + 1) if 0 < i < len(session.clips)]
    before = [transition_harmony(session, i) for i in adjacent]
    base = {"suggestion": None, "neighbors": before}
    if not adjacent:
        return {**base, "reason": "Add another song to check how this key blends."}
    if not _key(track.key):
        return {**base, "reason": "Supply this song's key before suggesting a pitch change."}
    if max_shift <= 0:
        return {**base, "reason": "Pitch suggestions are off in Mix review. Manual pitch changes are still available."}
    known = [item for item in before if item["state"] in ("compatible", "clash")]
    if not known:
        return {**base, "reason": "No musical neighbor with a known key to match; keep the original pitch and listen."}
    current_bad = sum(item["state"] == "clash" for item in before)
    choices = []
    for shift in range(-max_shift, max_shift + 1):
        if shift == clip.shift:
            continue
        after = [transition_harmony(session, i, {clip.id: shift}) for i in adjacent]
        if any(a["state"] == "compatible" and b["state"] != "compatible" for a, b in zip(before, after)):
            continue
        bad = sum(item["state"] == "clash" for item in after)
        # Return to the source key only if compatibility does not become worse.
        if bad < current_bad or (shift == 0 and clip.shift and bad == current_bad):
            choices.append((bad, abs(shift), abs(shift - clip.shift), shift, after))
    if not choices:
        reason = ("Current key labels fit the neighboring blends; no pitch change needed."
                  if not current_bad else
                  f"No shift within ±{max_shift} st improves these blends without worsening another. "
                  "Try different cues, a shorter overlap, or another song order.")
        return {**base, "reason": reason}
    _, _, _, shift, after = min(choices, key=lambda item: item[:4])
    improved = [item["index"] for a, item in zip(before, after) if a["state"] == "clash" and item["state"] == "compatible"]
    protected = [item["index"] for item in before if item["state"] == "compatible"]
    unchecked = [item["index"] for item in after if item["state"] in ("unknown", "percussion")]
    remaining = [item["index"] for item in after if item["state"] == "clash"]
    pairs = lambda values: ", ".join(f"{i} → {i + 1}" for i in values)
    reason = (f"Helps {pairs(improved)} by key labels." if improved else "Original pitch keeps the same key compatibility.")
    if protected:
        reason += f" Keeps {pairs(protected)} compatible."
    if unchecked:
        reason += f" Listen to {pairs(unchecked)}; its harmony is not verified."
    if remaining:
        reason += f" {pairs(remaining)} still has a possible key clash."
    suggestion = {"semitones": shift, "resulting_key": djmix.key_name(djmix.transpose(_key(track.key), shift)),
                  "reason": reason, "after": after, "improved": improved}
    return {**base, "suggestion": suggestion, "reason": reason}


def review_mix(session, preferred_percent=6.0, max_shift=2):
    """A current, actionable report. No single score pretends to rate the music."""
    from studio_guidance import timing_guidance
    from studio_overlap_content import review_voice_overlaps, voice_advice
    try:
        voices = review_voice_overlaps(session)
        voice_error = ''
    except djmix.MixError as error:
        voices = {}; voice_error = str(error)
    tempo = tempo_advice(session, preferred_percent)
    songs, transitions = [], []
    for index, clip in enumerate(session.clips):
        track = session.tracks[clip.track_id]
        notes = []
        if not Path(track.path).is_file():
            notes.append(("missing", "Source file is missing. Use Reconnect audio… to choose the original at its new location before listening or exporting."))
        if track.bpm:
            from studio_tempo_curve import clip_tempo_range
            try:
                limits = clip_tempo_range(session, index)
            except djmix.MixError as error:
                notes.append(('layout', 'Tempo range cannot be checked yet. ' + str(error)))
            else:
                change = max(((bpm / track.bpm - 1) * 100 for bpm in limits), key=abs)
                if abs(change) > preferred_percent:
                    notes.append(("tempo", f"Tempo changes by up to {change:+.1f}%, beyond your preferred {preferred_percent:g}% limit."))
        if abs(clip.shift) > max_shift:
            notes.append(("pitch", f"Pitch changes by {clip.shift:+d} st, beyond your suggested-shift limit. Listen for altered voices or instruments."))
        guidance = timing_guidance(track.timing, clip.stabilize, track=track)
        if guidance.title == 'Timing needs updating':
            notes.append(('timing', 'Saved timing does not match the source audio. Reconnect missing audio or Analyze timing again before using correction or its BPM suggestion.'))
        elif guidance.title in ("Timing inconclusive", "Timing not analyzed"):
            notes.append(("timing", guidance.title + ". Check the source BPM and listen for beats drifting apart."))
        elif guidance.available and not clip.stabilize:
            notes.append(("correction", "Tempo correction is available but off. Compare A/B before deciding whether it helps."))
        if notes:
            songs.append({"id": clip.id, "name": track.name, "number": index + 1, "notes": notes})
    for index in range(1, len(session.clips)):
        item = transition_harmony(session, index)
        item["id"] = session.clips[index].id
        item["suggestion"] = None
        evidence = voices.get(item['id'], {'state': 'not_checked', 'seconds': 0.})
        item['voices'] = {**evidence, 'can_check': not bool(voice_error),
                          'reason': ('Voices cannot be checked yet. ' + voice_error) if voice_error else voice_advice(evidence)}
        if item["state"] == "clash":
            candidates = []
            for candidate_index in (index, index - 1):
                advice = pitch_advice(session, candidate_index, max_shift)["suggestion"]
                if advice and index in advice["improved"]:
                    candidate = session.clips[candidate_index]
                    candidates.append({**advice, "id": candidate.id,
                                       "name": session.tracks[candidate.track_id].name})
            if candidates:
                item["suggestion"] = min(candidates, key=lambda value: (abs(value["semitones"]), -len(value["improved"])))
        transitions.append(item)
    count = (sum(len(song["notes"]) for song in songs) + sum(t["state"] in ("clash", "unknown") for t in transitions)
             + sum(t['voices']['state'] == 'possible_conflict' for t in transitions))
    pending = sum(t['voices']['state'] == 'not_checked' for t in transitions)
    return {"tempo": tempo, "songs": songs, "transitions": transitions, "count": count, 'voice_pending': pending}
