"""Cancellable subprocesses and bounded streaming reads, including Windows pipes."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
import threading

_CANCEL = ContextVar('dj_process_cancel', default=None)


def check_cancelled():
    event = _CANCEL.get()
    if event is not None and event.is_set():
        from djmix import MixError
        raise MixError('Operation cancelled.')


@contextmanager
def cancel_on(event):
    token = _CANCEL.set(event)
    try: yield
    finally: _CANCEL.reset(token)


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()


def run_command(command, options):
    check_cancelled()
    if _CANCEL.get() is None:
        return subprocess.run(command, capture_output=True, **options)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options) as process:
        try:
            while True:
                check_cancelled()
                try:
                    stdout, stderr = process.communicate(timeout=.1)
                    check_cancelled()
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired: pass
        finally: _stop(process)


def stream_command(command, options, chunk_bytes):
    """At most two queued chunks and 3000 bytes of diagnostic text.

    Reader threads make cancellation independent of a blocked pipe read. On
    cancellation, decoder failure, or an early consumer exit, reap the child
    and both readers before releasing its pipes. Nothing is left running.
    """
    if not isinstance(chunk_bytes, int) or not 1 <= chunk_bytes <= 4 * 1024 * 1024:
        raise ValueError('Choose a stream chunk between one byte and four MiB.')
    check_cancelled()
    queue = Queue(maxsize=2); stopped = threading.Event(); errors = bytearray()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options) as process:
        def put(value):
            while not stopped.is_set():
                try: queue.put(value, timeout=.1); return
                except Full: pass

        def output():
            try:
                while not stopped.is_set():
                    chunk = process.stdout.read(chunk_bytes)
                    if not chunk: break
                    put(chunk)
            except Exception as error: put(error)
            finally: put(None)

        def diagnostic():
            try:
                while chunk := process.stderr.read(4096):
                    errors.extend(chunk); del errors[:-3000]
            except (OSError, ValueError): pass

        readers = [threading.Thread(target=f, name='dj-audio-pipe', daemon=True) for f in (output, diagnostic)]
        for reader in readers: reader.start()
        try:
            while True:
                check_cancelled()
                try: value = queue.get(timeout=.1)
                except Empty: continue
                if value is None: break
                if isinstance(value, Exception): raise value
                yield value
            while process.poll() is None:
                check_cancelled()
                try: process.wait(timeout=.1)
                except subprocess.TimeoutExpired: pass
            readers[1].join()
            if process.returncode:
                from djmix import MixError
                raise MixError(f'{Path(command[0]).name} failed:\n{bytes(errors).decode(errors="replace")}')
        finally:
            stopped.set(); _stop(process)
            for reader in readers: reader.join()
