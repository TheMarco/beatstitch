"""One PCM clock for tape-monitor-style A/B auditioning.

Files are decoded on track selection. A/B and click toggles never load files,
seek, pause, or restart the sink. All mixing and sink writes run on Qt's main
thread; Qt owns the device thread and its ring buffer.
"""
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QtAudio

RATE = 48000
FRAME_BYTES = 8  # Stereo float32.
FADE_FRAMES = 240  # Five milliseconds, linear to avoid boosting correlated A/B.


class ABMixer:
    def __init__(self, a, b, click):
        if a.shape != b.shape or a.shape != click.shape or a.ndim != 2 or a.shape[1] != 2:
            raise ValueError("A, B and click must have matching stereo frame counts.")
        self.a, self.b, self.click = a, b, click
        self.frame = 0
        self.mix = self.target_mix = 0.0
        self.click_mix = self.target_click = 0.0

    @property
    def frames(self):
        return len(self.a)

    def select(self, variant):
        if variant not in ("fixed", "straightened"):
            raise ValueError("Unknown audition variant.")
        self.target_mix = float(variant == "straightened")

    def set_click(self, enabled):
        self.target_click = float(bool(enabled))

    def seek(self, frame):
        self.frame = max(0, min(int(frame), self.frames))
        self.mix, self.click_mix = self.target_mix, self.target_click

    @staticmethod
    def ramp(current, target, frames):
        step = np.arange(1, frames + 1, dtype=np.float32) / FADE_FRAMES
        return current + np.sign(target - current) * np.minimum(step, abs(target - current))

    def read(self, frames):
        end = min(self.frames, self.frame + int(frames))
        n = end - self.frame
        if n <= 0:
            return b""
        selection = slice(self.frame, end)
        blend = self.ramp(self.mix, self.target_mix, n)
        click = self.ramp(self.click_mix, self.target_click, n)
        output = (self.a[selection] * (1 - blend[:, None]) + self.b[selection] * blend[:, None]
                  + self.click[selection] * click[:, None])
        self.frame = end
        self.mix, self.click_mix = float(blend[-1]), float(click[-1])
        return np.asarray(output, dtype="<f4").tobytes()


class ABTransport(QObject):
    positionChanged = Signal(int)
    playingChanged = Signal(bool)
    errorOccurred = Signal(str)

    def __init__(self, parent=None, sink_factory=QAudioSink):
        super().__init__(parent)
        self.sink_factory = sink_factory
        self.sink = self.stream = self.mixer = None
        self.device_id = None
        self.pending = b""
        self.playing = False
        self.origin = 0
        self.variant, self.click = "fixed", False
        self.volume = .65
        self.last_reported = -100
        self.start_count = self.reset_count = 0
        self.format = QAudioFormat()
        self.format.setSampleRate(RATE)
        self.format.setChannelCount(2)
        self.format.setSampleFormat(QAudioFormat.SampleFormat.Float)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self._pump)

    def _set_playing(self, playing):
        if playing != self.playing:
            self.playing = playing
            self.playingChanged.emit(playing)

    def _reset(self):
        self.timer.stop()
        if self.sink is not None:
            self.sink.reset()
            self.reset_count += 1
        self.stream = None
        self.pending = b""

    def position_frame(self):
        if self.mixer is None:
            return 0
        if self.stream is None:
            return self.mixer.frame
        sent = self.mixer.frame - len(self.pending) // FRAME_BYTES
        processed = self.origin + round(self.sink.processedUSecs() * RATE / 1_000_000)
        return max(self.origin, min(sent, processed))

    def position(self):
        return round(self.position_frame() * 1000 / RATE)

    def load(self, folder: Path):
        self._set_playing(False)
        self._reset()
        self.mixer = None
        decoded = []
        for name in ("fixed", "straightened", "fixed-click"):
            audio, rate = sf.read(folder / f"{name}.wav", dtype="float32", always_2d=True)
            if rate != RATE or audio.shape[1] != 2 or not len(audio) or not np.isfinite(audio).all():
                raise ValueError("Auditions must contain finite stereo audio at 48 kHz.")
            decoded.append(audio)
        if len({len(a) for a in decoded}) != 1:
            raise ValueError("The audition files do not have equal lengths.")
        a, b, click_version = decoded
        # Both rendered click versions contain the same reference. Extract once
        # so its position/gain remains continuous when switching between A and B.
        click_version -= a
        self.mixer = ABMixer(a, b, click_version)
        self.mixer.select(self.variant)
        self.mixer.set_click(self.click)
        self.mixer.seek(0)
        self.origin = 0
        self.positionChanged.emit(0)

    def set_device(self, device):
        identity = None if device is None or device.isNull() else bytes(device.id())
        if identity == self.device_id and (self.sink is not None or identity is None):
            return
        position, was_playing = self.position_frame(), self.playing
        self._set_playing(False)
        self._reset()
        if self.sink is not None:
            self.sink.deleteLater()
        self.sink = None
        self.device_id = identity
        if self.mixer is not None:
            self.mixer.seek(position)
        if identity is None:
            self.errorOccurred.emit("The selected audio output is unavailable.")
            return
        if not device.isFormatSupported(self.format):
            self.device_id = None
            self.errorOccurred.emit("This output does not support stereo 48 kHz float audio.")
            return
        self.sink = self.sink_factory(device, self.format, self)
        self.sink.setVolume(self.volume)
        # Qt's default is much longer; this bounds A/B response without restarting
        # the device. Bluetooth may add its own latency after this shared buffer.
        self.sink.setBufferSize(round(.04 * RATE) * FRAME_BYTES)
        if was_playing:
            self.play()

    def select(self, variant):
        self.variant = variant
        if self.mixer is not None:
            self.mixer.select(variant)

    def set_click(self, enabled):
        self.click = bool(enabled)
        if self.mixer is not None:
            self.mixer.set_click(enabled)

    def set_volume(self, volume):
        self.volume = max(0, min(1, float(volume)))
        if self.sink is not None:
            self.sink.setVolume(self.volume)

    def play(self):
        if self.mixer is None or self.sink is None or self.playing:
            return
        if self.mixer.frame == self.mixer.frames and self.stream is None:
            self.mixer.seek(0)
        if self.stream is None:
            self.origin = self.mixer.frame
            self.stream = self.sink.start()
            self.start_count += 1
            if self.stream is None or self.sink.error() != QtAudio.Error.NoError:
                self._fail("Could not start the selected audio output.")
                return
        else:
            self.sink.resume()
        self._set_playing(True)
        self._pump()
        if self.playing:
            self.timer.start()

    def pause(self):
        if self.playing:
            self.timer.stop()
            self.sink.suspend()
            self._set_playing(False)
            self.positionChanged.emit(self.position())

    def seek(self, milliseconds):
        if self.mixer is None:
            return
        was_playing = self.playing
        self._set_playing(False)
        self._reset()
        self.mixer.seek(round(milliseconds * RATE / 1000))
        self.positionChanged.emit(self.position())
        if was_playing and self.mixer.frame < self.mixer.frames:
            self.play()

    def _fail(self, message):
        position = self.position_frame()
        self._set_playing(False)
        self._reset()
        if self.mixer is not None:
            self.mixer.seek(position)
        self.errorOccurred.emit(message)

    def _pump(self):
        if not self.playing or self.stream is None:
            return
        if self.sink.error() != QtAudio.Error.NoError:
            self._fail("Audio output stopped. Reconnect or choose an available output.")
            return
        capacity = self.sink.bytesFree()
        # Keep unwritten bytes on a partial write; the shared cursor never skips
        # or repeats samples, even if a backend accepts less than requested.
        if not self.pending and capacity >= FRAME_BYTES:
            self.pending = self.mixer.read(capacity // FRAME_BYTES)
        if self.pending and capacity:
            written = self.stream.write(self.pending[:capacity])
            if written < 0:
                self._fail("Could not write audio to the selected output.")
                return
            self.pending = self.pending[written:]
        position = self.position()
        if abs(position - self.last_reported) >= 40:
            self.last_reported = position
            self.positionChanged.emit(position)
        if (self.mixer.frame == self.mixer.frames and not self.pending and
                self.sink.state() == QtAudio.State.IdleState and
                self.position_frame() >= self.mixer.frames):
            self._set_playing(False)
            self._reset()
            self.positionChanged.emit(self.position())

    def close(self):
        self._set_playing(False)
        self._reset()
