"""Cached, loudness-matched timing audition using the production render path."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time

import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QButtonGroup, QCheckBox, QSlider, QDialogButtonBox)

import djmix
from studio_ab_audio import ABTransport
from studio_cache import AudioCache, digest, source_signature
from studio_engine import Session, layout, render_session
from studio_timing import corrected_time, mapping, RENDER_VERSION


def prepare_comparison(session, selected, cache_dir, progress=lambda message: None):
    clip = copy.deepcopy(selected)
    track = copy.deepcopy(session.tracks[clip.track_id])
    clip.stabilize = True
    if mapping(track, clip) is None:
        raise djmix.MixError('No confident correction is available for this track.')
    choices = [r for r in track.timing['regions']
               if min(clip.cue_out, r['end'])-max(clip.cue_in, r['start']) >= 8]
    if not choices:
        raise djmix.MixError('The selected cue range has no confidently detected drift to compare.')
    chosen = max(choices, key=lambda r: r['p95_ms']*(min(clip.cue_out, r['end'])-max(clip.cue_in, r['start'])))
    source_start = max(clip.cue_in, chosen['start']-.5)
    solo = Session(target_bpm=session.target_bpm, tracks={track.id: track}, clips=[clip])
    ratio = track.bpm/session.target_bpm
    starts, available = {}, []
    for variant in ('fixed', 'straightened'):
        clip.stabilize = variant == 'straightened'
        start = round((corrected_time(track, clip, source_start)-corrected_time(track, clip, clip.cue_in))*ratio*djmix.SR)
        starts[variant] = start
        available.append(layout(solo)[0]['end']-start)
    frames = min(round(90*djmix.SR), *available)
    if frames < 4*djmix.SR:
        raise djmix.MixError('Widen the cue range for a timing comparison.')
    cache = AudioCache(cache_dir)
    key = [RENDER_VERSION, cache.engine, source_signature(track.path), track.timing, track.bpm,
           session.target_bpm, clip.cue_in, clip.cue_out, clip.shift, starts, frames]
    folder = cache.root/'auditions'/digest(key)
    report_file = folder/'report.json'
    if report_file.exists():
        try:
            report = json.loads(report_file.read_text())
            if all(sf.info(folder/f'{name}.wav').frames == frames for name in ('fixed', 'straightened', 'fixed-click')):
                os.utime(report_file, None)
                return report
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.audition-', dir=folder.parent) as temporary:
        stage = Path(temporary)
        audio, measurements = {}, {}
        for variant in ('fixed', 'straightened'):
            progress('Preparing A · fixed BPM' if variant == 'fixed' else 'Preparing B · stabilized tempo')
            clip.stabilize = variant == 'straightened'
            result = render_session(solo, stage/f'{variant}.wav', cache_dir=cache_dir,
                                    region=(starts[variant], starts[variant]+frames), progress=progress)
            audio[variant], _ = sf.read(result['path'], dtype='float32', always_2d=True)
            measurements[variant] = result
        # Equal loudness and shared click; enough headroom for switching and clicks.
        loudness = min(-18, *(m['lufs']-m['true_peak_db']-3 for m in measurements.values()))
        for variant in audio:
            audio[variant] *= 10**((loudness-measurements[variant]['lufs'])/20)
            sf.write(stage/f'{variant}.wav', audio[variant], djmix.SR, subtype='PCM_24')
        reference = audio['fixed'].copy()
        period = 60/track.timing['bpm']*ratio
        clip.stabilize = True
        first = (chosen['grid'][0]-corrected_time(track, clip, source_start))*ratio
        first += max(0, np.ceil(-first/period))*period
        t = np.arange(round(.022*djmix.SR))/djmix.SR
        tick = .075*np.sin(2*np.pi*1600*t)*np.exp(-t*220)
        for point in np.arange(first, frames/djmix.SR, period):
            pos = round(point*djmix.SR)
            length = min(len(tick), frames-pos)
            if length > 0:
                reference[pos:pos+length] += tick[:length, None]
        sf.write(stage/'fixed-click.wav', reference, djmix.SR, subtype='PCM_24')
        report = {'folder': str(folder), 'name': track.name, 'source_start': source_start,
                  'duration': frames/djmix.SR, 'target_bpm': session.target_bpm,
                  'effective_bpm': 60/period, 'shift': clip.shift, 'created': time.time()}
        (stage/'report.json').write_text(json.dumps(report, allow_nan=False))
        # Publish complete files; repair an incomplete prior cache entry in place.
        folder.mkdir(exist_ok=True)
        for path in sorted(stage.iterdir(), key=lambda p: p.name == 'report.json'):
            os.replace(path, folder/path.name)
    cache.prune()
    return report


class TimingComparison(QDialog):
    def __init__(self, report, output_preference, volume, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Compare tempo correction')
        self.setMinimumWidth(620)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.output_preference = output_preference
        self.player = ABTransport(self)
        self.devices = QMediaDevices(self)
        box = QVBoxLayout(self); box.setContentsMargins(26, 24, 26, 24); box.setSpacing(16)
        title = QLabel(report['name']); title.setProperty('role', 'title'); box.addWidget(title)
        start = report['source_start']
        info = QLabel(f"{report['target_bpm']:g} BPM project, {report['shift']:+d} semitones, "
                      f"from {int(start)//60}:{int(start)%60:02d} in the source.\n"
                      'Switch between A and B while it plays. Loudness is matched.')
        info.setWordWrap(True); info.setProperty('role', 'secondary'); box.addWidget(info)
        row = QHBoxLayout(); self.group = QButtonGroup(self)
        for text, variant in [('A: fixed BPM', 'fixed'), ('B: stabilized tempo', 'straightened')]:
            button = QPushButton(text); button.setCheckable(True); button.setMinimumHeight(56)
            self.group.addButton(button); row.addWidget(button)
            button.clicked.connect(lambda checked=False, v=variant: self.player.select(v))
            if variant == 'fixed': button.setChecked(True)
        box.addLayout(row)
        click = QCheckBox('Steady reference click'); click.toggled.connect(self.player.set_click); box.addWidget(click)
        row = QHBoxLayout(); self.play = QPushButton('Play'); self.play.clicked.connect(self.toggle)
        row.addWidget(self.play)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setRange(0, round(report['duration']*1000))
        self.seek.sliderReleased.connect(lambda: self.player.seek(self.seek.value())); row.addWidget(self.seek, 1)
        self.time = QLabel('0:00'); row.addWidget(self.time); box.addLayout(row)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setProperty('role', 'secondary'); box.addWidget(self.status)
        footer = QLabel('This comparison uses your current cue, pitch and project tempo settings. '
                        'Close it, then turn on Stabilize tempo in the clip panel to use the correction.')
        footer.setProperty('role', 'hint'); footer.setWordWrap(True); box.addWidget(footer)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject); box.addWidget(close)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playingChanged.connect(lambda playing: self.play.setText('Pause' if playing else 'Play'))
        self.player.errorOccurred.connect(self.status.setText)
        self.devices.audioOutputsChanged.connect(self.refresh_output)
        self.player.load(Path(report['folder']))
        self.player.set_volume(volume)
        self.refresh_output()
        self.finished.connect(lambda _: self.player.close())

    def refresh_output(self):
        device = QMediaDevices.defaultAudioOutput() if self.output_preference == 'system' else next(
            (d for d in QMediaDevices.audioOutputs() if bytes(d.id()).hex() == self.output_preference), None)
        self.player.set_device(device)
        if self.player.sink is not None:
            self.status.setText('Output: '+device.description())

    def position_changed(self, ms):
        if not self.seek.isSliderDown(): self.seek.setValue(ms)
        self.time.setText(f'{ms//60000}:{ms//1000%60:02d}')

    def toggle(self):
        if self.player.playing: self.player.pause()
        else: self.player.play()
