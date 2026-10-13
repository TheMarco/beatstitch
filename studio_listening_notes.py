"""Saved subjective listening notes bound to exact audio recipes.

A note records what the user heard in the normal interface. It is not a
calibrated reference label. No processing or file writes happen here.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone

import djmix
from studio_cache import source_signature
from studio_engine import layout, preview_range
from studio_render_cache import listening_recipe
from studio_source_identity import valid_fingerprint

JUDGMENT_KEYS = ("timing", "harmony", "content")
JUDGMENT_VALUES = frozenset({"unchecked", "fits", "problem", "unsure"})
MAX_NOTES = 2000
MAX_COMMENT = 2000
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")
_NOTE_KEYS = frozenset({"version", "id", "pair", "fingerprint", "recorded_at",
                        "judgments", "comment", "provenance", "estimate_exposure",
                        "reference_annotation", "sources", "recipe"})
_SOURCE_KEYS = frozenset({"clip_id", "track_id", "source_signature", "sha256"})
_RECIPE_KEYS = frozenset({"start", "duration", "reference_end", "fingerprint"})


def _fail(message):
    raise djmix.MixError(message)


def _is_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _valid_id(value):
    if not isinstance(value, str):
        return False
    if _HEX32.fullmatch(value):
        return True
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def _valid_time(value):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def _validate_sources(sources):
    if not isinstance(sources, list):
        _fail("Listening-note sources must be a list.")
    for entry in sources:
        if not isinstance(entry, dict) or not set(entry) <= _SOURCE_KEYS:
            _fail("Each listening-note source needs clip, track, signature and hash fields.")
        if not isinstance(entry.get("clip_id"), str) or not entry["clip_id"]:
            _fail("Each listening-note source needs a clip identity.")
        if not isinstance(entry.get("track_id"), str) or not entry["track_id"]:
            _fail("Each listening-note source needs a track identity.")
        sig = entry.get("source_signature")
        if (not isinstance(sig, list) or len(sig) != 3 or not isinstance(sig[0], str)
                or not sig[0] or type(sig[1]) is not int or type(sig[2]) is not int
                or sig[1] < 0 or sig[2] < 0):
            _fail("Each listening-note source needs a file signature of path, size and modification time.")
        sha = entry.get("sha256")
        if sha is not None and not (isinstance(sha, str) and _HEX64.fullmatch(sha)):
            _fail("A listening-note source hash must be a 64-character hex value or none.")


def _validate_recipe(recipe, fingerprint):
    if not isinstance(recipe, dict) or not set(recipe) <= _RECIPE_KEYS:
        _fail("Listening-note recipe details are invalid.")
    if not _is_number(recipe.get("start")) or recipe["start"] < 0:
        _fail("A listening note needs a valid recipe start.")
    if not _is_number(recipe.get("duration")) or recipe["duration"] <= 0:
        _fail("A listening note needs a valid recipe duration.")
    if "reference_end" in recipe and (not _is_number(recipe["reference_end"])
                                       or recipe["reference_end"] <= recipe["start"]):
        _fail("Listening-note recipe details are invalid.")
    if "fingerprint" in recipe:
        other = recipe["fingerprint"]
        if not (isinstance(other, str) and _HEX64.fullmatch(other)):
            _fail("Listening-note recipe details are invalid.")
        if other.lower() != fingerprint.lower():
            _fail("Listening-note recipe details do not match its fingerprint.")


def validate_notes(value):
    """Validate a list of saved notes, raising MixError for malformed data."""
    if not isinstance(value, list):
        _fail("Saved listening notes must be a list.")
    if len(value) > MAX_NOTES:
        _fail("A project can hold at most 2000 listening notes.")
    seen = set()
    for note in value:
        if not isinstance(note, dict):
            _fail("Each listening note must be an object.")
        for key in note:
            lowered = str(key).lower()
            if "confidence" in lowered or "percent" in lowered:
                _fail("Listening notes cannot contain detector confidence or percentages.")
        if not set(note) <= _NOTE_KEYS:
            _fail("A listening note contains an unsupported field.")
        required = {"version", "id", "pair", "fingerprint", "recorded_at",
                    "judgments", "comment", "provenance", "estimate_exposure",
                    "reference_annotation"}
        if not required <= set(note):
            _fail("A listening note is missing required details.")
        if type(note["version"]) is not int or note["version"] != 1:
            _fail("Unsupported listening-note version.")
        if not _valid_id(note["id"]):
            _fail("Each listening note needs a UUID identity.")
        identity = note["id"].lower().replace("-", "")
        if identity in seen:
            _fail("Listening-note identities must be unique.")
        seen.add(identity)
        pair = note["pair"]
        if (not isinstance(pair, list) or len(pair) != 2
                or any(not isinstance(item, str) or not item for item in pair)
                or pair[0] == pair[1]):
            _fail("A listening note needs an outgoing and an incoming clip.")
        if not (isinstance(note["fingerprint"], str) and _HEX64.fullmatch(note["fingerprint"])):
            _fail("A listening note needs its audio-recipe fingerprint.")
        if not _valid_time(note["recorded_at"]):
            _fail("A listening note needs a UTC recording time.")
        judgments = note["judgments"]
        if not isinstance(judgments, dict) or set(judgments) != set(JUDGMENT_KEYS):
            _fail("A listening note needs exactly timing, harmony and content judgments.")
        if any(not isinstance(judgments[key], str) or judgments[key] not in JUDGMENT_VALUES for key in JUDGMENT_KEYS):
            _fail("Listening judgments must be unchecked, fits, problem or unsure.")
        if not isinstance(note["comment"], str) or len(note["comment"]) > MAX_COMMENT:
            _fail("A listening-note comment must be text of at most 2000 characters.")
        if note["provenance"] != "user_listening_note":
            _fail("A listening note must come from user listening.")
        if note["estimate_exposure"] != "normal_interface":
            _fail("A listening note must record normal-interface estimate exposure.")
        if note["reference_annotation"] is not False:
            _fail("A listening note is not a reference annotation.")
        if "sources" in note:
            _validate_sources(note["sources"])
        if "recipe" in note:
            _validate_recipe(note["recipe"], note["fingerprint"])
    return value


def _clean_judgments(judgments):
    if judgments is None:
        judgments = {}
    if not isinstance(judgments, dict):
        _fail("Judgments must give timing, harmony and content choices.")
    unknown = set(judgments) - set(JUDGMENT_KEYS)
    if unknown:
        _fail("Judgments can only cover timing, harmony and content.")
    result = {}
    for key in JUDGMENT_KEYS:
        value = judgments.get(key, "unchecked")
        if not isinstance(value, str) or value not in JUDGMENT_VALUES:
            _fail("Listening judgments must be unchecked, fits, problem or unsure.")
        result[key] = value
    return result


def _contributors(session, index):
    positions = layout(session)
    start, end = preview_range(session, index)
    result = []
    for clip, pos in zip(session.clips, positions):
        if max(start, pos["start"]) < min(end, pos["end"]):
            result.append(clip)
    return result


def capture_note(session, index, loaded_fingerprint, judgments=None, comment=""):
    """Capture one validated note for the blend the user just heard."""
    if type(index) is not int:
        _fail("Select a transition to save a listening note.")
    clips = getattr(session, "clips", None)
    if not isinstance(clips, list) or not 1 <= index < len(clips):
        _fail("Select a transition to save a listening note.")
    clean_judgments = _clean_judgments(judgments)
    if not isinstance(comment, str) or len(comment) > MAX_COMMENT:
        _fail("A listening-note comment must be text of at most 2000 characters.")
    try:
        recipe = listening_recipe(session, "transition", index)
    except OSError as exc:
        raise djmix.MixError("A source song is unavailable, so this blend cannot be noted yet.") from exc
    if not isinstance(loaded_fingerprint, str) or loaded_fingerprint != recipe["fingerprint"]:
        _fail("This blend changed since it was loaded. Listen to the current blend before saving a note.")
    try:
        contributors = session.clips if session.mastering_enabled else _contributors(session, index)
        sources = []
        for clip in contributors:
            track = session.tracks[clip.track_id]
            signature = source_signature(track.path)
            sha = None
            stored = getattr(track, "fingerprint", None)
            if valid_fingerprint(stored) and list(stored["source"]) == list(signature):
                sha = stored["sha256"]
            sources.append({"clip_id": clip.id, "track_id": clip.track_id,
                            "source_signature": list(signature), "sha256": sha})
    except OSError as exc:
        raise djmix.MixError("A source song is unavailable, so this blend cannot be noted yet.") from exc
    except KeyError as exc:
        raise djmix.MixError("A source song is unavailable, so this blend cannot be noted yet.") from exc
    # Capture must describe one stable source/recipe snapshot even if an audio
    # file changes while its provenance is being read.
    if listening_recipe(session, "transition", index)['fingerprint'] != recipe['fingerprint']:
        _fail('Source audio changed while saving. Listen to the current blend before saving notes.')
    note = {
        "version": 1,
        "id": uuid.uuid4().hex,
        "pair": [clips[index - 1].id, clips[index].id],
        "fingerprint": recipe["fingerprint"],
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "judgments": clean_judgments,
        "comment": comment,
        "provenance": "user_listening_note",
        "estimate_exposure": "normal_interface",
        "reference_annotation": False,
        "recipe": {"start": recipe["start"], "duration": recipe["duration"],
                   "reference_end": recipe["reference_end"], "fingerprint": recipe["fingerprint"]},
        "sources": sources,
    }
    validate_notes([note])
    return note


def note_status(note, session):
    """Return current, changed, unavailable or removed for a saved note."""
    try:
        pair = note["pair"]
        fingerprint = note["fingerprint"]
    except (TypeError, KeyError):
        return "unavailable"
    if (not isinstance(pair, list) or len(pair) != 2
            or not isinstance(fingerprint, str) or not _HEX64.fullmatch(fingerprint)):
        return "unavailable"
    clips = getattr(session, "clips", None)
    if not isinstance(clips, list):
        return "unavailable"
    pairs = [(a.id, b.id) for a, b in zip(clips, clips[1:])]
    try:
        index = pairs.index((pair[0], pair[1])) + 1
    except ValueError:
        return "removed"
    try:
        current = listening_recipe(session, "transition", index)["fingerprint"]
    except (OSError, ValueError, KeyError, TypeError, djmix.MixError):
        return "unavailable"
    return "current" if current == fingerprint else "changed"


def latest_note(session, index):
    """Return the most recent saved note for the pair now at ``index``."""
    if type(index) is not int:
        return None
    clips = getattr(session, "clips", None)
    if not isinstance(clips, list) or not 1 <= index < len(clips):
        return None
    pair = [clips[index - 1].id, clips[index].id]
    notes = getattr(session, "listening_notes", None)
    if not isinstance(notes, list):
        return None
    best, best_key = None, None
    for position, note in enumerate(notes):
        if not isinstance(note, dict) or note.get("pair") != pair:
            continue
        stamp = note.get("recorded_at")
        try:
            moment = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if isinstance(stamp, str) else 0.0
        except (ValueError, OSError, OverflowError):
            moment = 0.0
        key = (moment, position)
        if best_key is None or key > best_key:
            best, best_key = note, key
    return best
