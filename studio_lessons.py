"""Bundled, bounded listening examples. Opening a lesson never edits a session."""
import hashlib
import json
from pathlib import Path
import sys

import soundfile as sf

import djmix

LESSONS = (
    {
        'id': 'timing', 'title': '1 · Hear drifting beats',
        'a': 'A · Drifting beats', 'b': 'B · Steady beats', 'topic': 'timing',
        'listen': 'Listen for the extra kick pulling away from the regular beat. Switch to B: the two drum patterns stay together. The difference grows toward the end.',
        'change': 'The incoming drum pattern gradually speeds up in A. B places those same drum hits on a steady 120 BPM grid. This is an authored illustration, not an automatic-correction benchmark.',
        'try': 'In your mix: check the source BPM and beat grid first. Under More settings, use Analyze beside Stabilize to check for drift, then A/B to hear an available correction. Tempo matching alone cannot straighten drift.',
    },
    {
        'id': 'keys', 'title': '2 · Hear clashing notes',
        'a': 'A · Clashing notes', 'b': 'B · Related notes', 'topic': 'keys',
        'listen': 'The repeating melody rubs against the held chord in A. In B, its notes fit the chord. Switch in the middle of a phrase and listen to the melody, not just the drums.',
        'change': 'The chord stays in C minor. A’s melody is in C-sharp minor; B lowers that melody by one semitone into C minor. The example uses original synthesized parts at the same tempo.',
        'try': 'In your mix: select the incoming song and review the key advice. Try a small suggested pitch change and compare the blend. Keep the original key if you prefer it. Matching key labels cannot guarantee a good musical fit.',
    },
    {
        'id': 'bass', 'title': '3 · Hear competing basslines',
        'a': 'A · Basslines overlap', 'b': 'B · Bass handed over', 'topic': 'transitions',
        'listen': 'Around the middle, A lets both bass patterns overlap. B keeps one low end in charge and passes it to the next song. This difference is easier to hear on headphones or speakers that reproduce bass.',
        'change': 'Both versions use the same two synthesized songs and the same crossfade. B uses DJ Studio’s 180 Hz split and one-bar bass handoff at the blend midpoint. Whole-example listening levels are matched.',
        'try': 'In your mix: select a numbered transition, open the blend editor, and compare Bass handoff on and off. Shorter overlaps or different cues may also help when two musical parts compete.',
    },
)


def asset_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets/lessons'


def lesson_audio(identity, root=None):
    if identity not in {item['id'] for item in LESSONS}:
        raise djmix.MixError('Choose one of the listening examples.')
    folder = (Path(root) if root is not None else asset_root()) / identity
    try:
        report = json.loads((folder / 'lesson.json').read_text())
        if report['version'] != 1 or report['id'] != identity: raise ValueError('Unknown lesson version.')
        frames = []
        for name in ('current', 'draft'):
            path = folder / f'{name}.wav'; info = sf.info(path)
            # Check sizes before decoding into the shared-clock transport.
            if info.samplerate != djmix.SR or info.channels != 2 or not djmix.SR <= info.frames <= 20 * djmix.SR:
                raise ValueError('Invalid lesson duration or format.')
            if path.stat().st_size > 20 * djmix.SR * 8 + 4096: raise ValueError('Lesson file is too large.')
            if hashlib.sha256(path.read_bytes()).hexdigest() != report['sha256'][name]:
                raise ValueError('Lesson audio has changed.')
            frames.append(info.frames)
        if frames[0] != frames[1]: raise ValueError('The examples have different lengths.')
        return {'folder': str(folder), 'frames': frames[0], 'duration': frames[0] / djmix.SR}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise djmix.MixError('This listening example is missing or damaged. Reinstall a complete copy '
                             'of DJ Studio to restore the included audio.') from error
