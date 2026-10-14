"""Independent per-song checks exposed through a shared native batch workflow."""
from pathlib import Path

import djmix

OPERATIONS = {
    'keys': ('Detect source keys', ('key',)),
    'tempo': ('Analyze BPM and keys', ('key',)),
    'timing': ('Analyze tempo drift', ()),
    'beats': ('Detect beats and musical cues', ('beats',)),
    'content': ('Detect voice and drum passages', ('content',)),
    'sections': ('Detect builds and drops', ('sections',)),
}


def observe(track, kind, data, progress):
    path, data = Path(track.path), Path(data)
    if kind in ('timing', 'beats') and not track.bpm:
        raise djmix.MixError('Estimate or enter this song’s BPM first.')
    if kind == 'timing':
        from studio_timing import analyze
        return analyze(path, track.bpm, track.duration, data / 'timing-cache', progress)
    if kind == 'beats':
        from studio_structure import analyze_song
        return analyze_song(path, track.bpm, track.duration, data / 'structure-cache', progress)
    if kind == 'content':
        from studio_content import analyze_content
        return analyze_content(path, track.duration, data / 'content-cache', progress)
    if kind == 'sections':
        from studio_song_sections import analyze_sections
        return analyze_sections(path, track.duration, data / 'section-cache', progress)
    raise djmix.MixError('Choose a supported song check.')


def show_results(studio):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout
    batch = getattr(studio, 'last_library_batch', None)
    if not batch:
        return
    dialog = QDialog(studio)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setWindowTitle('Song batch results'); dialog.resize(800, 500)
    column = QVBoxLayout(dialog)
    heading = QLabel(OPERATIONS[batch['kind']][0]); column.addWidget(heading)
    table = QTreeWidget(); table.setRootIsDecorated(False)
    table.setHeaderLabels(['Song', 'Result', 'Details']); table.setColumnWidth(0, 220); table.setColumnWidth(1, 110)
    table.setAccessibleName('Per-song batch results')
    retry = []
    for row in batch['rows']:
        item = QTreeWidgetItem([row['name'], row['state'], row['detail']])
        for index in range(3): item.setToolTip(index, row['detail'])
        table.addTopLevelItem(item)
        if row['state'] in ('Failed', 'Uncertain', 'Not run'):
            retry.append(row['identity'])
    column.addWidget(table, 1)
    detail = QLabel('Select a song to read its full result.'); detail.setWordWrap(True)
    detail.setTextFormat(Qt.TextFormat.PlainText); detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    table.currentItemChanged.connect(lambda item, _: detail.setText(item.text(2) if item else ''))
    column.addWidget(detail)
    buttons = QHBoxLayout()
    again = QPushButton(f'Retry unfinished ({len(retry)})')
    again.setEnabled(bool(retry) and not studio.jobs)
    again.setToolTip('Retry only failed, inconclusive or unprocessed songs. Finished checks keep their cached results.')
    def rerun():
        dialog.close()
        studio.analyze_library_tracks(retry, kind=batch['kind'])
    again.clicked.connect(rerun); buttons.addWidget(again); buttons.addStretch()
    close = QPushButton('Close'); close.clicked.connect(dialog.close); buttons.addWidget(close)
    column.addLayout(buttons)
    dialog.show()
    return dialog


def labels(studio, identities, key=False):
    from PySide6.QtWidgets import QInputDialog
    from studio_engine import cache_track
    from studio_key import KEYS, set_manual_key
    if studio.jobs: return
    tracks = [studio.session.tracks[i] for i in identities if i in studio.session.tracks]
    if not tracks: return
    if key:
        value, ok = QInputDialog.getItem(studio, 'Set source key labels',
            f'Assign one source key label to {len(tracks)} song(s). This corrects metadata; it does not transpose audio.',
            ['Unknown'] + sorted(KEYS), editable=False)
    else:
        value, ok = QInputDialog.getDouble(studio, 'Set source BPM labels',
            f'Assign one source BPM to {len(tracks)} song(s). Existing cue positions are kept.', tracks[0].bpm or 128, 20, 300, 3)
    if not ok: return
    def mutate():
        for track in tracks:
            if key: set_manual_key(track, '' if value == 'Unknown' else value)
            else: track.bpm, track.estimated = value, False
    if studio.edit(mutate, action=f'Set source {"key" if key else "BPM"} labels for {len(tracks)} song(s)'):
        studio.touched.update(t.id for t in tracks)
        for track in tracks:
            try: cache_track(track, studio.data / 'library-cache')
            except OSError as error: studio.notify(str(error), 'error')
        studio.say('Source labels saved for the selection. Cue positions are kept. Pitch choices are unchanged.')


def clip_value(studio, identities, field, value=None):
    from PySide6.QtWidgets import QInputDialog
    from studio_engine import overlap_limit
    from studio_guidance import timing_guidance
    if studio.jobs: return
    clips = [c for c in studio.session.clips if c.track_id in identities]
    if not clips: return
    skipped = 0
    if field == 'pitch' and value is None:
        value, ok = QInputDialog.getInt(studio, 'Set pitch shifts',
            f'Set the pitch shift of {len(clips)} selected mix clip(s), in semitones. Major stays major; minor stays minor.', 0, -12, 12)
        if not ok: return
    if field == 'blend_bars':
        value, ok = QInputDialog.getInt(studio, 'Set overlap length',
            'Incoming overlaps of the selected mix songs, in bars. Short songs use their maximum available overlap.', 8, 1, 32)
        if not ok: return
    if field in ('blend_bars', 'transition_effect', 'bass_swap', 'balance_levels'):
        clips = [c for c in clips if studio.session.clips.index(c) > 0]
    if field == 'stabilize' and value:
        eligible = [c for c in clips if timing_guidance(studio.session.tracks[c.track_id].timing,
                    track=studio.session.tracks[c.track_id]).available]
        skipped = len(clips) - len(eligible); clips = eligible
    if not clips:
        studio.say('No eligible mix clips. Analyze tempo drift first to see which songs can be stabilized.' if field == 'stabilize' else
                   'No incoming overlaps in this selection.')
        return
    capped = []
    def mutate():
        for clip in clips:
            if field == 'pitch': clip.semitones, clip.transpose = value, value != 0
            elif field == 'blend_bars':
                limit = overlap_limit(studio.session, studio.session.clips.index(clip))['bars']
                clip.blend_bars = min(value, limit)
                if value > limit: capped.append(studio.session.tracks[clip.track_id].name)
            else: setattr(clip, field, value)
    if studio.edit(mutate, action=f'Change {field.replace("_", " ")} for {len(clips)} mix clip(s)'):
        studio.say(f'Updated {len(clips)} mix clip(s). Play mix or Listen to blend to prepare and hear the changes.'
                   + (f' {skipped} clip(s) have no supported tempo correction and were kept.' if skipped else '')
                   + (f' {len(capped)} overlap(s) were limited by available audio.' if capped else ''))


def reveal_sources(studio, identities):
    import subprocess
    import sys
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    paths = list(dict.fromkeys(Path(studio.session.tracks[i].path) for i in identities if i in studio.session.tracks))
    if sys.platform == 'darwin':
        subprocess.run(['open', '-R', *map(str, paths)], check=False)
    elif sys.platform == 'win32':
        for path in paths: subprocess.run(['explorer', '/select,', str(path)], check=False)
    else:
        for folder in dict.fromkeys(path.parent for path in paths):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
