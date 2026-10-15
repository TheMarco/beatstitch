"""Reviewable blend alternatives and aligned, level-matched current/draft audio."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

import djmix
from studio_cache import digest
from studio_engine import layout, overlap_limit
from studio_recommendations import pitch_advice, transition_harmony
from studio_render_cache import audio_recipe, prepare_listening
from studio_storage import managed_cache, require_space
from studio_transition_audio import BlendMixer, BlendTransport

from studio_fades import FIELDS as FADE_FIELDS, token as fade_token

DRAFT_FIELDS = ('blend_bars', 'bass_swap', 'transpose', 'semitones', 'balance_levels', 'transition_effect',
                'cue_in', 'intro_percussive', *FADE_FIELDS)


def sound_settings(clip):
    return (clip.blend_bars, clip.bass_swap, clip.shift, clip.balance_levels, clip.transition_effect,
            clip.cue_in, tuple(fade_token(clip) or ()))


def draft_session(session, index, draft):
    if not 0 < index < len(session.clips):
        raise djmix.MixError('Select a transition between two songs.')
    original = session.clips[index]
    if (draft.id, draft.track_id) != (original.id, original.track_id):
        raise djmix.MixError('The selected song changed. Reopen this blend.')
    result = copy.deepcopy(session)
    for name in DRAFT_FIELDS:
        setattr(result.clips[index], name, getattr(draft, name))
    layout(result)
    return result


def alternatives(session, index, max_shift=2):
    """Starting points to audition, never a claim of automatic musical quality."""
    original = session.clips[index]
    limit = overlap_limit(session, index)['bars']
    choices, seen = [], set()

    def add(label, reason, **settings):
        clip = copy.deepcopy(original)
        for name, value in settings.items():
            setattr(clip, name, value)
        signature = sound_settings(clip)
        if signature not in seen:
            draft_session(session, index, clip)
            seen.add(signature)
            choices.append({'label': label, 'reason': reason, 'clip': clip})

    add('Current mix', 'Your existing blend. Choose an alternative, or adjust the controls below.')
    presets = (1, 2, 4, 8, 16, 32)
    shorter = [n for n in presets if n < original.blend_bars]
    longer = [n for n in presets if original.blend_bars < n <= limit]
    if shorter:
        n = max(shorter)
        add(f'Shorter overlap · {n} bars', 'Less time with both songs playing. Try this when melodies, vocals or rhythms compete; '
            'listen for an abrupt handover.', blend_bars=n)
    if longer:
        n = min(longer)
        add(f'Longer overlap · {n} bars', 'A more gradual handover. Listen for competing vocals or bass and matching musical phrases. '
            'Available audio alone does not guarantee a good blend.', blend_bars=n)
    add('Crossfade the bass' if original.bass_swap else 'Swap bass at the middle',
        'Compare a full-overlap bass crossfade with a one-bar handoff at the middle. Listen for a muddy or thin low end.',
        bass_swap=not original.bass_swap)
    add('Keep whole-song levels' if original.balance_levels else 'Balance levels inside this blend',
        'Compare the song levels with a gentle reduction of the louder side inside this overlap. '
        'Limited to 3 dB, with smooth ramps and no compression or boosts to quiet passages.',
        balance_levels=not original.balance_levels)
    from studio_transition_effects import EFFECTS
    for kind, (title, reason) in EFFECTS.items():
        if kind != original.transition_effect:
            add(title, reason + ' Compare it with the current mix before applying.', transition_effect=kind)
    if original.shift:
        add('Keep the original key', 'Turn off pitch shifting for the whole incoming song. Tempo matching remains on; '
            'listen to this blend and the next one.', transpose=False)
    suggestion = pitch_advice(session, index, max_shift)['suggestion']
    if suggestion:
        shift = suggestion['semitones']
        add(f'Try pitch {shift:+d} st · {suggestion["resulting_key"]}' if shift else 'Keep the original key',
            suggestion['reason'] + ' Key labels are only a guide. Compare the sound before applying.',
            transpose=bool(shift), semitones=shift if shift else original.semitones)
    return choices


def change_summary(session, index, draft):
    original = session.clips[index]
    from studio_tempo_curve import session_curve
    curve = session_curve(session)
    ending = layout(session)[index - 1]['end'] / djmix.SR
    seconds = float(curve.to_output(ending) - curve.to_output(ending - draft.blend_bars * 240 / session.target_bpm))
    pitch = f'{draft.shift:+d} st' if draft.shift else 'original key'
    bass = 'bass handoff at the middle' if draft.bass_swap else 'bass crossfade throughout'
    text = f'{draft.blend_bars} bars ({seconds:.1f} sec), {bass}, incoming song at {pitch}.'
    from studio_fades import panel_values
    fades = panel_values(draft)
    text += (f' Outgoing fade: {fades["out_bars"]:g} bars, {fades["out_curve"]}. '
             f'Incoming fade: {fades["in_bars"]:g} bars, {fades["in_curve"]}.')
    text += ' Gentle level balance inside this blend.' if draft.balance_levels else ' Whole-song levels, no extra blend balancing.'
    from studio_transition_effects import EFFECTS
    if draft.transition_effect != 'none': text += ' Outgoing finish: ' + EFFECTS[draft.transition_effect][0] + '.'
    if draft.blend_bars != original.blend_bars:
        delta = (original.blend_bars - draft.blend_bars) * 240 / session.target_bpm
        text += (f' Later songs move {abs(delta):.1f} sec {"later" if delta > 0 else "earlier"}.' if curve.identity else
                 ' Later songs move on the musical timeline; elapsed-time changes follow the tempo curve.')
    if draft.shift != original.shift:
        text += ' Pitch changes the whole incoming clip.'
        candidate = draft_session(session, index, draft)
        if index + 1 < len(session.clips):
            following = transition_harmony(candidate, index + 1)
            text += f' Check {index + 1} → {index + 2} too: {following["reason"]}'
    if draft.cue_in != original.cue_in:
        delta = draft.cue_in - original.cue_in
        text += (f' Incoming Start {draft.cue_in:.3f} s: '
                 f'{"omits" if delta > 0 else "restores"} {abs(delta):.1f} source seconds at the beginning. '
                 'The ending stays fixed; later songs move with the changed duration. '
                 'The drums-only intro label is cleared for a newly suggested cue.')
    return text


def comparison_region(current, draft, index):
    """Anchor both versions to the outgoing song, with no neighboring blend."""
    positions = [layout(s) for s in (current, draft)]
    anchor = positions[0][index - 1]['end']
    if positions[1][index - 1]['end'] != anchor:
        raise djmix.MixError('The outgoing song must stay in place for a seamless comparison.')
    earliest = max(p[index - 1]['start'] + p[index - 1]['incoming'] for p in positions)
    latest = min(p[index + 1]['start'] if index + 1 < len(p) else p[index]['end'] for p in positions)
    context = round(4 * 240 / current.target_bpm * djmix.SR)
    start = max(earliest, anchor - max(p[index]['incoming'] for p in positions) - context)
    end = min(latest, anchor + context)
    if end - start < djmix.SR:
        raise djmix.MixError('There is not enough audio between neighboring blends for this comparison.')
    return start, end


@managed_cache
def prepare_choice_comparison(session, index, draft, cache_dir, progress=lambda message: None):
    candidate = draft_session(session, index, draft)
    region = comparison_region(session, candidate, index)
    recipes = [audio_recipe(s, region) for s in (session, candidate)]
    frames = round(recipes[0]['duration'] * djmix.SR)
    key = [1, *(r['fingerprint'] for r in recipes)]
    folder = Path(cache_dir) / 'alternatives' / digest(key)
    report = {'folder': str(folder), 'start': region[0] / djmix.SR, 'end': region[1] / djmix.SR,
              'duration': frames / djmix.SR, 'key': key, 'cached': False}
    try:
        saved = json.loads((folder / 'report.json').read_text())
        infos = [sf.info(folder / f'{name}.wav') for name in ('current', 'draft')]
        if saved['key'] == key and all(i.frames == frames and i.samplerate == djmix.SR and i.channels == 2 for i in infos):
            os.utime(folder / 'report.json', None)
            progress('Reusing this current/draft comparison')
            for name in ('current', 'draft'):
                field = name + '_level_balance'
                saved[field] = [{**item, 'index': index} for item in saved.get(field, [])]
            return {**saved, **report, 'cached': True}
    except (OSError, ValueError, KeyError, RuntimeError):
        pass
    folder.parent.mkdir(parents=True, exist_ok=True)
    require_space(folder.parent, frames * 16, 'prepare the blend comparison')
    with tempfile.TemporaryDirectory(prefix='.audition-', dir=folder.parent) as temporary:
        stage = Path(temporary)
        renders = []
        for name, version in (('current', session), ('draft', candidate)):
            progress(f'Preparing {name} blend')
            renders.append(prepare_listening(version, Path(cache_dir).parent / 'renders', region, progress, cache_dir))
        # One constant gain per version: match perceived level without compression.
        target = min(-18, *(r['lufs'] - r['true_peak_db'] - 2 for r in renders))
        for name, render in zip(('current', 'draft'), renders):
            report[name + '_level_balance'] = render.get('level_balance', [])
            progress(f'Matching {name} listening level')
            audio, rate = sf.read(render['path'], dtype='float32', always_2d=True)
            if rate != djmix.SR or audio.shape != (frames, 2) or not np.isfinite(audio).all():
                raise djmix.MixError('The comparison audio is incomplete. Try preparing it again.')
            gain = target - render['lufs']
            audio *= 10 ** (gain / 20)
            sf.write(stage / f'{name}.wav', audio, djmix.SR, subtype='PCM_24')
            report[name + '_gain_db'] = gain
        if [audio_recipe(s, region)['fingerprint'] for s in (session, candidate)] != key[1:]:
            raise djmix.MixError('A source changed while preparing the comparison. Please try again.')
        report['listening_lufs'] = target
        (stage / 'report.json').write_text(json.dumps(report, allow_nan=False))
        progress('Comparison ready')
        folder.mkdir(exist_ok=True)
        for path in sorted(stage.iterdir(), key=lambda p: p.name == 'report.json'):
            os.replace(path, folder / path.name)
    return report


class ChoiceMixer(BlendMixer):
    WEIGHTS = {'current': (1., 0.), 'draft': (0., 1.)}

    def __init__(self, current, draft):
        super().__init__(current, draft)
        self.gains = self.target = (1., 0.)


class ChoiceTransport(BlendTransport):
    FILES = ('current', 'draft')
    MIXER = ChoiceMixer
    DEFAULT = 'current'
