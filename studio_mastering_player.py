#!/usr/bin/env python3
"""Native Qt A/B player for the isolated mastering experiment."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPainter, QColor, QPen
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from studio_mastering_audio import MasteringTransport, load_mastering_folder, RATE

TITLE = "DJ Studio — Mastering Lab"


def fmt_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


class WaveformView(QWidget):
    """Real peaks for A and effective B on one shared amplitude scale."""

    BUCKETS = 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(84)
        self.setAccessibleName("Waveform preview")
        self._peaks = {}
        self._scale = 1.0
        self._fraction = 0.0

    @classmethod
    def _bucket_peaks(cls, audio):
        if audio is None or not len(audio):
            return None
        if hasattr(audio, 'peaks'): return audio.peaks(cls.BUCKETS)
        mono = np.max(np.abs(audio), axis=1)
        n = min(cls.BUCKETS, len(mono))
        edges = np.linspace(0, len(mono), n + 1).astype(int)
        return np.array([mono[edges[i]:edges[i + 1]].max() if edges[i + 1] > edges[i] else 0.0
                         for i in range(n)], dtype=np.float64)

    def set_data(self, a_audio, b_audio):
        self.set_peaks(self._bucket_peaks(a_audio), self._bucket_peaks(b_audio))

    def set_peaks(self, a_peaks, b_peaks):
        self._peaks = {'A': a_peaks, 'B': b_peaks}
        # Keep A's displayed height unchanged when matching is toggled.
        self._scale = 1.0
        self.update()

    def set_fraction(self, fraction):
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#16211d"))
        usable = max(1, self.width() - 28)
        for row, (key, color) in enumerate((("A", QColor("#93c6b2")), ("B", QColor("#f0c987")))):
            peaks = self._peaks.get(key)
            if peaks is None:
                continue
            mid = (row + .5) * self.height() / 2
            painter.setPen(QPen(QColor('#33453d'), 1))
            painter.drawLine(28, int(mid), self.width(), int(mid))
            painter.setPen(QPen(color, 1))
            painter.drawText(7, int(mid + 5), key)
            for x in range(28, self.width()):
                peak = float(peaks[min(len(peaks) - 1, (x - 28) * len(peaks) // usable)])
                half = peak / self._scale * (self.height() / 4 - 4)
                painter.drawLine(x, int(mid - half), x, int(mid + half))
        x = 28 + int(self._fraction * max(0, self.width() - 29))
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawLine(x, 0, x, self.height())


class MasteringLab(QWidget):
    def __init__(self, folder=None, diagnostics=False, smoke_test=False, player=None, *, parent=None,
                 embedded=False, output_preference='system', volume=60, prepared=None):
        super().__init__(parent)
        self.folder = Path(folder) if folder is not None else None
        self.diagnostics = bool(diagnostics)
        self.smoke_test = bool(smoke_test)
        self.ready = False
        self.report = None
        self.data = None
        self._waveform_peaks = {}
        self.prepared = prepared
        self.output_preference = output_preference
        self.initial_volume = max(0, min(100, volume))
        self.embedded = embedded
        self.devices = QMediaDevices(self)
        self.player = player if player is not None else MasteringTransport(self)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playingChanged.connect(self.play_state)
        self.player.errorOccurred.connect(lambda m: self.status.setText(m))
        self.devices.audioOutputsChanged.connect(self.refresh_outputs)
        self.setWindowTitle(TITLE)
        self.resize(820, 640)
        self.setMinimumSize(720, 560)
        self._build_ui()
        if self.embedded:
            self.setStyleSheet('')  # Follow the native studio's existing theme.
            self.a_button.setText('A · Before mastering')
            self.b_button.setText('B · After mastering')
            self.a_button.setAccessibleName(self.a_button.text())
            self.b_button.setAccessibleName(self.b_button.text())
            self.waveform.setToolTip('A is before final mastering; B is after it. Both rows use the same fixed amplitude scale. With matching on, the louder version is attenuated for a fair comparison.')
        if self.smoke_test:
            self.player.set_volume(0.0)  # muted; never opens a device
        else:
            self.refresh_outputs()
        self._load()
        self._apply_ready_state()

    # -- UI ---------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(14)
        eyebrow = QLabel("DJ STUDIO  /  MASTERING LAB")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("Compare the finishing pass")
        title.setObjectName("title")
        layout.addWidget(title)
        intro = QLabel("This is an experimental final finishing pass on the current mix. "
                       "Volume matching helps you judge punch and texture instead of extra loudness — "
                       "the louder master can sound better simply because it is louder.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if self.embedded:
            eyebrow.hide(); title.hide(); intro.hide()
        self.track_name = QLabel("No mastering folder loaded")
        self.track_name.setObjectName("track")
        layout.addWidget(self.track_name)
        self.detail = QLabel()
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        passage_row = QHBoxLayout()
        passage_row.addWidget(QLabel("Passage"))
        self.passage = QComboBox()
        self.passage.setAccessibleName("Passage selector")
        self.passage.setToolTip("Choose the whole mix or a report segment. Changing passage may seek and change loop boundaries.")
        self.passage.addItem("Whole mix")
        self.passage.currentIndexChanged.connect(self.change_passage)
        passage_row.addWidget(self.passage, 1)
        layout.addLayout(passage_row)

        self.waveform = WaveformView()
        self.waveform.setToolTip("A is your current mix; B is the mastered version. Both rows use the same fixed amplitude scale. With loudness matching on, B shows the quieter comparison copy.")
        layout.addWidget(self.waveform)

        choices = QHBoxLayout()
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.a_button = QPushButton("A · Current mix")
        self.b_button = QPushButton("B · Mastered")
        for btn, tip in ((self.a_button, "Hear the current mix (A). Shortcut: A or 1."),
                         (self.b_button, "Hear the mastered version (B). Shortcut: B or 2. "
                                         "Uses the loudness-matched master while 'Match listening loudness' is on.")):
            btn.setCheckable(True)
            btn.setMinimumHeight(58)
            btn.setAccessibleName(btn.text())
            btn.setToolTip(tip)
            group.addButton(btn)
            choices.addWidget(btn)
        self.a_button.setChecked(True)
        self.a_button.clicked.connect(lambda: self.choose("A"))
        self.b_button.clicked.connect(lambda: self.choose("B"))
        layout.addLayout(choices)

        self.match = QCheckBox("Match listening loudness")
        self.match.setChecked(True)
        self.match.setAccessibleName("Match listening loudness")
        self.match.setToolTip("On: B is the mastered version attenuated to the current mix loudness, "
                              "so you judge punch and texture. Off: B is the louder finished master. "
                              "Switching is live and does not restart playback.")
        self.match.toggled.connect(self.on_match_toggled)
        layout.addWidget(self.match)

        transport = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.setMinimumWidth(86)
        self.play.setAccessibleName("Play or pause")
        self.play.setToolTip("Play or pause. Shortcut: Space.")
        self.play.clicked.connect(self.toggle_play)
        transport.addWidget(self.play)
        restart = QPushButton("Restart")
        restart.setAccessibleName("Restart passage")
        restart.setToolTip("Restart the selected passage.")
        restart.clicked.connect(lambda: self.player.seek(0))
        transport.addWidget(restart)
        self.loop = QCheckBox("Loop")
        self.loop.setAccessibleName("Loop passage")
        self.loop.setToolTip("Loop the selected passage.")
        self.loop.toggled.connect(self.player.set_loop)
        transport.addWidget(self.loop)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Playback position")
        self.seek.setToolTip("Seek within the selected passage.")
        self.seek.sliderReleased.connect(lambda: self.player.seek(self.seek.value()))
        transport.addWidget(self.seek, 1)
        self.time = QLabel("0:00 / 0:00")
        self.time.setMinimumWidth(90)
        transport.addWidget(self.time)
        layout.addLayout(transport)

        self.status = QLabel("Press Play, then switch A/B freely. Playback stays continuous.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Output"))
        self.output = QComboBox()
        self.output.setAccessibleName("Audio output device")
        self.output.setToolTip("Audio output device. System default follows your OS setting.")
        self.output.currentIndexChanged.connect(self.choose_output)
        bottom.addWidget(self.output, 1)
        bottom.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setAccessibleName("Volume")
        self.volume.setToolTip("Playback volume.")
        self.volume.setRange(0, 100)
        self.volume.setValue(0 if self.smoke_test else self.initial_volume)
        self.volume.setMaximumWidth(110)
        self.volume.valueChanged.connect(lambda v: self.player.set_volume(v / 100))
        bottom.addWidget(self.volume)
        layout.addLayout(bottom)
        footnote = QLabel("Shortcuts: A / 1 for Current, B / 2 for Mastered, Space for play/pause. "
                          "Your original recordings are unchanged.")
        footnote.setObjectName("muted")
        footnote.setWordWrap(True)
        layout.addWidget(footnote)
        self.setStyleSheet("""
            QWidget { background: #111a19; color: #e4eeea; font-size: 14px; }
            QLabel#title { font-size: 26px; font-weight: 650; }
            QLabel#track { font-size: 16px; font-weight: 600; }
            QLabel#eyebrow { color: #93c6b2; font-size: 12px; font-weight: 650; }
            QLabel#muted { color: #9bb0a8; }
            QPushButton, QComboBox { background: #1e2b27; border: 1px solid #3c5149;
                border-radius: 7px; min-height: 38px; padding: 0 12px; }
            QPushButton:hover { border-color: #9cddbf; }
            QPushButton:checked { background: #b5edcd; color: #112d20; border-color: #b5edcd; }
            QComboBox QAbstractItemView { background: #1e2b27; selection-background-color: #40614e; }
            QSlider::groove:horizontal { background: #33453d; height: 5px; border-radius: 2px; }
            QSlider::handle:horizontal { background: #b5edcd; width: 13px; margin: -5px 0; border-radius: 6px; }
            QCheckBox { spacing: 10px; min-height: 25px; }
        """)

    def _apply_ready_state(self):
        for widget in (self.a_button, self.b_button, self.match, self.play, self.seek, self.loop, self.passage):
            widget.setEnabled(self.ready)

    # -- loading ------------------------------------------------------------
    def _load(self):
        if self.folder is None:
            self.status.setText("No mastering folder given. Pass --folder PATH with report.json and WAV files.")
            return
        try:
            # Load once via the transport; keep arrays for the waveform.
            self.data = self.prepared[0] if self.prepared is not None else load_mastering_folder(self.folder)
            self.player.load_data(self.data)
        except (OSError, ValueError, RuntimeError) as exc:
            self.ready = False
            self.report = None
            self.status.setText(f"Could not load mastering audition: {exc}")
            self.track_name.setText("Mastering audition unavailable")
            return
        self.ready = True
        self.report = self.data.report
        self.track_name.setText(self.report.name)
        r = self.report
        metrics = (
            f"{fmt_time(r.duration)}  ·  Current {r.current.lufs:.1f} LUFS / {r.current.true_peak_db:.1f} dBTP  ·  "
            f"Mastered {r.mastered.lufs:.1f} LUFS / {r.mastered.true_peak_db:.1f} dBTP  ·  "
            f"Matched {r.matched.lufs:.1f} LUFS / {r.matched.true_peak_db:.1f} dBTP  ·  "
            f"Match gain {r.matched_gain_db:+.1f} dB")
        difference = r.mastered.lufs - (r.current.lufs - r.current_gain_db)
        direction = 'louder' if difference >= 0 else 'quieter'
        self.detail.setText(f'{fmt_time(r.duration)}  ·  The actual master is {abs(difference):.1f} dB {direction}. '
                            'Matching removes the volume difference for comparison.')
        self.detail.setToolTip(metrics)
        compressed = any(s.startswith('Gentle') and 'compression' in s for s in r.processing_summary)
        self.summary.setText('Gentle compression and peak control.' if compressed else 'Peak control with your original dynamics.')
        self.summary.setToolTip('\n'.join(r.processing_summary))
        self.passage.blockSignals(True)
        self.passage.clear()
        self.passage.addItem("Whole mix", (0, len(self.data.current)))
        for seg in r.segments:
            start = round(seg.start * RATE)
            end = round(seg.end * RATE)
            self.passage.addItem(f"{seg.name} ({fmt_time(seg.start)}–{fmt_time(seg.end)})", (start, end))
        self.passage.blockSignals(False)
        self.seek.setRange(0, round(r.duration * 1000))
        self.time.setText(f"0:00 / {fmt_time(r.duration)}")
        # Initial transport volume: 60% (or muted in smoke mode).
        self.player.set_volume(0.0 if self.smoke_test else self.initial_volume / 100)
        # Compute every passage/variant once before any playback. Scanning a
        # six-minute waveform on the main thread during a live match toggle
        # would starve Qt's audio pump despite retaining the same sink.
        if self.prepared is not None:
            self._waveform_peaks = self.prepared[1]
            self.prepared = None
        else:
            self._waveform_peaks.clear()
            for i in range(self.passage.count()):
                start, end = self.passage.itemData(i)
                self._waveform_peaks[(start, end)] = {
                    variant: self.waveform._bucket_peaks(self.data.array_for(variant)[start:end])
                    for variant in ('current', 'mastered', 'matched')}
        self._update_waveform()
        self.status.setText("Press Play, then switch A/B freely. Playback stays continuous.")

    def _passage_bounds(self):
        if not self.ready:
            return (0, 0)
        return self.passage.currentData() or (0, len(self.data.current))

    def _update_waveform(self):
        if not self.ready:
            return
        start, end = self._passage_bounds()
        peaks = self._waveform_peaks[(start, end)]
        self.waveform.set_peaks(peaks['current'], peaks[self.player.b_variant])

    # -- actions --------------------------------------------------------------
    @property
    def effective_variant(self):
        return self.player.effective

    def choose(self, selection):
        if not self.ready:
            return
        self.player.set_selection(selection)
        (self.a_button if selection == "A" else self.b_button).setChecked(True)
        self._log_switch()

    def on_match_toggled(self, enabled):
        if not self.ready:
            return
        self.player.set_match_loudness(bool(enabled))
        self._update_waveform()
        self._log_switch()

    def _log_switch(self):
        if self.diagnostics:
            print(json.dumps({"variant": self.player.effective, "playing": self.player.playing,
                              "position_ms": self.player.position(),
                              "sink_starts": self.player.start_count,
                              "sink_resets": self.player.reset_count}), flush=True)

    def change_passage(self, *_):
        if not self.ready:
            return
        start, end = self._passage_bounds()
        name = self.passage.currentText()
        self.player.set_passage(start, end, name)
        duration_ms = round((end - start) * 1000 / RATE)
        self.seek.setRange(0, duration_ms)
        self.time.setText(f"0:00 / {fmt_time((end - start) / RATE)}")
        self.waveform.set_fraction(0.0)
        self._update_waveform()

    def toggle_play(self):
        if not self.ready:
            return
        if self.player.playing:
            self.player.pause()
        else:
            if not self.smoke_test:
                self.refresh_outputs()
            self.player.play()

    def play_state(self, *_):
        self.play.setText("Pause" if self.player.playing else "Play")

    def position_changed(self, value):
        if not self.seek.isSliderDown():
            self.seek.setValue(value)
        start, end = self._passage_bounds()
        duration_s = (end - start) / RATE if self.ready else 0
        self.time.setText(f"{fmt_time(value / 1000)} / {fmt_time(duration_s)}")
        if duration_s:
            self.waveform.set_fraction(value / 1000 / duration_s)

    def is_playing(self):
        return self.player.playing

    # -- output (as TempoLab) ---------------------------------------------------
    def refresh_outputs(self):
        if self.smoke_test:
            return
        devices = QMediaDevices.audioOutputs()
        default = QMediaDevices.defaultAudioOutput()
        self.output.blockSignals(True)
        self.output.clear()
        self.output.addItem("System default · " + (default.description() or "Unavailable"), "system")
        for device in devices:
            self.output.addItem(device.description(), bytes(device.id()).hex())
        index = self.output.findData(self.output_preference)
        if index < 0:
            self.output.addItem("Selected output disconnected", self.output_preference)
            index = self.output.count() - 1
        self.output.setCurrentIndex(index)
        self.output.blockSignals(False)
        chosen = default if self.output_preference == "system" else next(
            (d for d in devices if bytes(d.id()).hex() == self.output_preference), None)
        self.player.set_device(chosen)

    def choose_output(self, index):
        if index < 0:
            return
        self.output_preference = self.output.itemData(index)
        self.refresh_outputs()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_A, Qt.Key.Key_1):
            self.choose("A")
        elif key in (Qt.Key.Key_B, Qt.Key.Key_2):
            self.choose("B")
        elif key == Qt.Key.Key_Space and not isinstance(self.focusWidget(), (QCheckBox, QComboBox, QSlider)):
            # Buttons keep their native Space activation.
            if isinstance(self.focusWidget(), QPushButton):
                super().keyPressEvent(event)
                return
            self.toggle_play()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def closeEvent(self, event):
        self.player.close()
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, help="Mastering folder with report.json and WAV files.")
    parser.add_argument("--diagnostics", action="store_true", help="Log A/B switch continuity checks.")
    parser.add_argument("--smoke-test", action="store_true",
                        help="Instantiate, validate and quit muted without opening an audio device.")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1] + (argv or []))
    app.setApplicationName("Mastering Lab")
    app.setFont(QFont("Arial", 13))
    window = MasteringLab(args.folder, diagnostics=args.diagnostics, smoke_test=args.smoke_test)
    if args.smoke_test:
        if not window.ready:
            print(f"smoke-test failed: {window.status.text()}", file=sys.stderr)
            return 2
        print(json.dumps({"smoke": "ok", "name": window.report.name,
                          "frames": len(window.data.current), "variant": window.effective_variant}))
        window.player.close()
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
