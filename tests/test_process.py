"""Stop must reap its own processes and pipe readers without leaking into later jobs."""
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import djmix
from studio_process import cancel_on, run_command, stream_command


class ProcessTests(unittest.TestCase):
    def test_cancel_running_command_reaps_child_and_context_is_reset(self):
        event = threading.Event(); children = []; original = subprocess.Popen
        def popen(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        timer = threading.Timer(.25, event.set); timer.start(); started = time.monotonic()
        try:
            with patch('studio_process.subprocess.Popen', side_effect=popen), cancel_on(event):
                with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                    run_command([sys.executable, '-c', 'import time; time.sleep(30)'], {})
        finally: timer.cancel(); timer.join()
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(children); self.assertIsNotNone(children[0].poll())
        result = run_command([sys.executable, '-c', 'print("ready")'], {})
        self.assertEqual(result.stdout.strip(), b'ready')

    def test_stream_drains_stderr_and_limits_chunks_and_error_tail(self):
        code = 'import sys; sys.stderr.buffer.write(b"x"*200000+b"last diagnostic"); sys.stderr.flush(); sys.stdout.buffer.write(b"a"*300000); sys.exit(3)'
        sizes = []
        with self.assertRaises(djmix.MixError) as raised:
            for chunk in stream_command([sys.executable, '-c', code], {}, 8192): sizes.append(len(chunk))
        self.assertEqual(sum(sizes), 300000); self.assertLessEqual(max(sizes), 8192)
        self.assertIn('last diagnostic', str(raised.exception)); self.assertLess(len(str(raised.exception)), 3100)

    def test_cancel_during_real_time_stretch_cleans_work_and_preserves_destination(self):
        from pathlib import Path
        import tempfile
        import numpy as np
        import soundfile as sf
        from studio_engine import Track, Clip, Session, render_session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'song.wav'; destination = root / 'mix.wav'
            tone = .1 * np.sin(2 * np.pi * 330 * np.arange(8 * djmix.SR) / djmix.SR)
            sf.write(source, np.column_stack((tone, tone)), djmix.SR)
            track = Track('song', str(source), 'Song', 8, 120, 'C minor', cue_out=8)
            session = Session(target_bpm=126, tracks={'song': track}, clips=[Clip('song', 0, 8)])
            destination.write_bytes(b'keep this export')
            event = threading.Event(); children = []; original = subprocess.Popen
            def popen(command, *args, **kwargs):
                child = original(command, *args, **kwargs)
                children.append(child)
                if Path(command[0]).stem == 'rubberband': event.set()
                return child
            with patch('studio_process.subprocess.Popen', side_effect=popen), cancel_on(event):
                with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                    render_session(session, destination, cache_dir=root / 'audio-cache')
            self.assertTrue(event.is_set(), 'Must reach the actual Rubber Band process')
            self.assertTrue(all(child.poll() is not None for child in children))
            self.assertEqual(destination.read_bytes(), b'keep this export')
            self.assertFalse(list(root.glob('dj-render-*')))
            self.assertFalse(list(root.rglob('.pending-*')))

    def test_blocked_stream_and_early_close_leave_no_child_or_reader(self):
        original = subprocess.Popen; children = []
        def popen(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        before = {t.ident for t in threading.enumerate() if t.name == 'dj-audio-pipe'}
        event = threading.Event(); timer = threading.Timer(.25, event.set); timer.start()
        try:
            with patch('studio_process.subprocess.Popen', side_effect=popen), cancel_on(event):
                with self.assertRaisesRegex(djmix.MixError, 'cancelled'):
                    list(stream_command([sys.executable, '-c', 'import time; time.sleep(30)'], {}, 8192))
        finally: timer.cancel(); timer.join()
        with patch('studio_process.subprocess.Popen', side_effect=popen):
            stream = stream_command([sys.executable, '-c', 'import sys\nwhile True: sys.stdout.buffer.write(b"a"*65536); sys.stdout.flush()'], {}, 8192)
            self.assertEqual(len(next(stream)), 8192); stream.close()
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertEqual({t.ident for t in threading.enumerate() if t.name == 'dj-audio-pipe'}, before)


if __name__ == '__main__': unittest.main()
