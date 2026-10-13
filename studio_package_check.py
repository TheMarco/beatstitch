"""Exercise a frozen distribution without opening or touching a user's project."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def bundled_tool(name, root, frozen):
    import djmix
    found = djmix.tool_path(name)
    require(found is not None, f'Missing {name}.')
    path = Path(found).resolve()
    if frozen:
        require(path.is_relative_to(root.resolve()),
                f'{name} was found outside the package: {path}. A system install cannot satisfy this check.')
    return path


def qt_decode(path, expected_seconds):
    """Decode through Qt's shipped multimedia backend, without playing sound."""
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder
    app = QCoreApplication.instance() or QCoreApplication([])
    decoder = QAudioDecoder()
    loop = QEventLoop()
    observed = {'frames': 0, 'seconds': 0., 'finished': False, 'error': ''}

    def consume():
        buffer = decoder.read()
        fmt = buffer.format()
        if not buffer.isValid() or fmt.channelCount() != 2 or fmt.sampleRate() != 48000:
            observed['error'] = 'Qt returned invalid audio or an unexpected channel count/sample rate.'
            loop.quit()
            return
        observed['frames'] += buffer.frameCount()
        observed['seconds'] += buffer.frameCount() / fmt.sampleRate()

    def finished():
        observed['finished'] = True
        loop.quit()

    def failed(*_):
        observed['error'] = decoder.errorString() or 'Qt audio decoding failed.'
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    decoder.bufferReady.connect(consume)
    decoder.finished.connect(finished)
    decoder.error.connect(failed)
    decoder.setSource(QUrl.fromLocalFile(str(path)))
    timer.start(15000)
    decoder.start()
    loop.exec()
    timer.stop()
    decoder.stop()
    require(not observed['error'], observed['error'])
    require(observed['finished'], 'Qt decoding timed out after 15 seconds.')
    require(abs(observed['seconds'] - expected_seconds) < .1,
            f'Qt decoded {observed["seconds"]:.3f}s; expected {expected_seconds:.3f}s.')
    # Keep the application alive until the decoder and its backend are disposed.
    del decoder
    return observed


def check_tempo_import(source_track, destination):
    """Exercise the actual import pipeline using fresh, isolated analysis caches."""
    from studio_engine import Track, Session, estimate_track
    from studio_structure import current_tempo_estimate, current_opening_tempo, MODEL_SHA256
    source = Path(source_track.path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    track = Track('package-tempo', str(source), source_track.name, source_track.duration,
                  key='F minor', key_origin='manual')
    estimate_track(track, Path(destination) / 'timing-cache')
    beat, opening = current_tempo_estimate(track), current_opening_tempo(track)
    require(track.status.startswith('Beat-based') and beat.get('bpm') is not None,
            'The packaged BPM import fell back instead of running the beat model successfully.')
    require(opening.get('bpm') is not None, 'The opening-passage tempo alternative was not retained.')
    require((track.key, track.key_origin) == ('F minor', 'manual'), 'Tempo import changed a manual key.')
    project = Session(tracks={track.id: track}).to_dict()
    restored = Session.from_dict(json.loads(json.dumps(project))).tracks[track.id]
    require(restored.bpm == track.bpm and current_tempo_estimate(restored) == beat
            and current_opening_tempo(restored) == opening, 'Tempo choices did not survive project serialization.')
    require(hashlib.sha256(source.read_bytes()).hexdigest() == before, 'Tempo analysis changed the source audio.')
    return {'bpm': track.bpm, 'beat_bpm': beat['bpm'], 'opening_bpm': opening['bpm'],
            'model_sha256': MODEL_SHA256, 'project_version': project['version'],
            'status': track.status, 'manual_key_preserved': True,
            'note': 'Tests offline import and saved tempo alternatives, not musical accuracy.'}


def run_checks(destination, *, allow_source=False):
    destination = Path(destination).resolve()
    # Refuse even an empty existing directory: no user file can be replaced.
    destination.mkdir(parents=True, exist_ok=False)
    frozen = bool(getattr(sys, 'frozen', False))
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    original_cwd = Path.cwd()
    original_numba_cache = os.environ.get('NUMBA_CACHE_DIR')
    if frozen:
        os.environ['NUMBA_CACHE_DIR'] = str(destination / 'numba-cache')
        os.chdir(root)
    report = {'version': 1, 'status': 'running', 'frozen': frozen,
              'platform': sys.platform, 'os': platform.platform(), 'architecture': platform.machine(),
              'checks': [], 'not_tested': [
                  'Physical audio output and device switching',
                  'Native window layout, keyboard operation and user workflows',
                  'Musical quality and analysis accuracy',
                  'Other model inference and optional section/separation packs',
                  'Other operating systems and architectures']}
    report_path = destination / 'report.json'

    def save():
        pending = destination / 'report.json.tmp'
        pending.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        pending.replace(report_path)

    def check(name, action):
        item = {'name': name, 'status': 'running'}
        report['checks'].append(item)
        save()
        began = time.monotonic()
        try:
            item['details'] = action()
            item['status'] = 'passed'
        except Exception as error:
            item.update(status='failed', error=f'{type(error).__name__}: {error}')
            raise
        finally:
            item['seconds'] = round(time.monotonic() - began, 3)
            save()

    save()
    try:
        def distribution():
            require(frozen or allow_source, 'Run this command from the packaged application; use --allow-source only for development.')
            return {'executable': sys.executable, 'resources': str(root), 'source_mode': not frozen}
        check('distribution', distribution)
        import djmix
        import numpy as np
        import soundfile as sf

        def tools():
            result = {}
            for name in ('ffmpeg', 'ffprobe', 'rubberband'):
                path = bundled_tool(name, root, frozen)
                version = djmix.run([str(path), '--version' if name == 'rubberband' else '-version'])
                result[name] = {'path': str(path), 'version':
                                (version.stdout + version.stderr).decode(errors='replace').splitlines()[0]}
            return result
        check('bundled audio executables', tools)

        def assets():
            from studio_content import verify_model
            from studio_structure import MODEL_SHA256, model_path
            from studio_lessons import LESSONS, lesson_audio
            from studio_key_model import verify_model as verify_key_model
            content = verify_model()
            key_model = verify_key_model()
            beat = model_path()
            require(hashlib.sha256(beat.read_bytes()).hexdigest() == MODEL_SHA256,
                    'The beat-analysis model is missing or damaged.')
            for lesson in LESSONS:
                lesson_audio(lesson['id'])
            return {'beat_model': beat.name, 'content_model': content.name, 'key_model': key_model.name, 'lessons': len(LESSONS)}
        check('core model and lesson assets', assets)

        def about_assets():
            from studio_about import asset_directory, load_credits
            from PySide6.QtGui import QImage
            catalog = load_credits()
            identities = {item['id'] for item in catalog['entries']}
            require({'qt', 'python', 'rubber-band', 'ffmpeg', 'openkeyscan', 'barlow', 'beat-this', 'panns'} <= identities,
                    'The bundled credits catalog is incomplete.')
            for name in ('beatstitch.png', 'beatstitch-icon.png'):
                require(not QImage(str(asset_directory() / name)).isNull(), f'The bundled {name} is invalid.')
            return {'credits': len(identities), 'creator': catalog['creator'], 'logo_and_icon': 'valid PNG'}
        check('About credits, logo, and app icon', about_assets)

        def pitch_tempo():
            t = np.arange(4 * djmix.SR) / djmix.SR
            tone = .1 * np.sin(2 * np.pi * 440 * t)
            source = destination / 'Tone source.wav'
            output = destination / 'Tone shifted.wav'
            sf.write(source, np.column_stack((tone, tone)), djmix.SR, subtype='FLOAT')
            djmix.transform(source, output, 120, 126, 2, formants=False)
            data, rate = sf.read(output, always_2d=True)
            require(rate == djmix.SR and data.shape[1] == 2 and np.isfinite(data).all(), 'Invalid transformed audio.')
            require(abs(len(data) - round(4 * 120 / 126 * rate)) <= 2, 'Tempo transformation has the wrong duration.')
            middle = data[rate:2 * rate, 0]
            hz = float(np.fft.rfftfreq(len(middle), 1 / rate)[np.argmax(abs(np.fft.rfft(middle * np.hanning(len(middle)))))])
            expected = 440 * 2 ** (2 / 12)
            require(abs(hz - expected) < 2, f'Pitch was {hz:.2f}Hz, expected {expected:.2f}Hz.')
            require(float(np.max(abs(data[:, 0] - data[:, 1]))) < 1e-5, 'Identical stereo channels lost coherence.')
            return {'frames': len(data), 'pitch_hz': hz, 'expected_pitch_hz': expected}
        check('independent pitch and tempo with stereo coherence', pitch_tempo)

        from studio_demo import bundled_project
        from studio_projects import read_project
        from studio_engine import preview_range, render_session
        from studio_tempo_curve import session_curve
        session, _ = read_project(bundled_project())
        require(len(session.clips) == 3, 'The bundled demo must contain three songs.')
        def source_key():
            from studio_key import analyze_key, valid_result
            source = Path(session.tracks[session.clips[0].track_id].path)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            result = analyze_key(source, destination / 'library-cache' / 'keys')
            require(valid_result(result) and result['state'] == 'estimated', 'The bundled key detector returned no usable estimate.')
            require(analyze_key(source, destination / 'library-cache' / 'keys') == result, 'Unchanged key analysis was not reused.')
            require(hashlib.sha256(source.read_bytes()).hexdigest() == before, 'Key analysis changed its source audio.')
            return {'key': result['candidates'][0]['key'], 'model_id': result['model_id'],
                    'frames': result['frames'], 'scores': len(result['candidates']),
                    'note': 'Tests offline inference and cache reuse, not musical accuracy.'}
        check('offline source-key worker and cache reuse', source_key)
        def cancelling_stereo():
            from studio_analysis_audio import mono_samples
            from studio_structure_features import decoded_audio, RATE
            import soundfile as sf
            rate = 48000; t = np.arange(rate * 9) / rate
            signal = .2 * np.sin(2*np.pi*80*t) + .1 * np.sin(2*np.pi*440*t)
            source = destination / 'cancelling-stereo.wav'
            sf.write(source, np.column_stack((signal, -signal)), rate, subtype='FLOAT')
            before = hashlib.sha256(source.read_bytes()).hexdigest(); details = {}
            audio = mono_samples(source, 44100, duration=9., details=details)
            require(len(audio) == 9*44100 and np.max(abs(audio)) > .1,
                    'Cancelling stereo lost its audible analysis signal.')
            require(details['analysis_channel'] == 'stronger_left', 'Unexpected analysis channel choice.')
            with decoded_audio(source, 9., destination/'phase-cache', lambda _: None) as (raw,count):
                require(count == 9*RATE, 'Beat frontend changed the source clock.')
                require(np.max(abs(np.fromfile(raw,dtype='<f4'))) > .1, 'Beat frontend lost stereo audio.')
            require(hashlib.sha256(source.read_bytes()).hexdigest() == before,
                    'Stereo analysis changed its playback source.')
            return {'sample_clock_preserved':True, 'source_unchanged':True,
                    'analysis_channel':details['analysis_channel']}
        check('cancelling stereo remains audible to analysis', cancelling_stereo)
        check('offline beat-based BPM import and saved alternatives', lambda: check_tempo_import(
            session.tracks[session.clips[0].track_id], destination / 'tempo-import'))
        region = preview_range(session, 1)
        # Exercise both Rubber Band engines, the musical clock and the real mixer.
        session.tempo_enabled = True
        session.tempo_points = [{'beat': 0., 'bpm': 124.}, {'beat': 160., 'bpm': 132.}]
        master = destination / 'Demo transition.wav'
        def render():
            result = render_session(session, master, region=region)
            data, rate = sf.read(master, always_2d=True)
            start, end = session_curve(session).output_span(*region)
            require(rate == djmix.SR and data.shape == (end - start, 2) and np.isfinite(data).all(),
                    'The demo render has invalid samples or duration.')
            require(np.max(abs(data)) > .001 and result['true_peak_db'] <= -.99, 'The demo is silent or clips.')
            return result
        check('bundled demo with gradual tempo', render)
        def mastering():
            from studio_mastering import master_file, prepare_comparison
            from studio_mastering_audio import prepare_playback
            finished = master_file(master, destination / 'Demo mastered.wav')
            require(finished['true_peak_db'] <= -1 and sf.info(finished['path']).frames == sf.info(master).frames,
                    'Automatic mastering clips or changes duration.')
            folder = destination / 'mastering-comparison'
            comparison = prepare_comparison(master, Path(finished['path']), folder,
                                             processing_summary=finished['mastering']['processing_summary'])
            require(abs(comparison['current']['lufs'] - comparison['matched']['lufs']) <= .15,
                    'Mastering A/B loudness is not matched.')
            data, peaks = prepare_playback(folder)
            try:
                require(len(peaks[(0, len(data.current))]['current']) == 1024,
                        'Streamed mastering waveform is incomplete.')
            finally: data.close()
            return {'lufs': finished['lufs'], 'true_peak_db': finished['true_peak_db'],
                    'frames': finished['frames'], 'matched_lufs': comparison['matched']['lufs'],
                    'streamed_playback_ready': True}
        check('automatic mastering and streamed volume-matched comparison', mastering)
        def listening_notes():
            import copy
            from studio_engine import Session
            from studio_listening_notes import capture_note, note_status
            from studio_listening_notes_ui import ListeningNotesDialog
            from studio_render_cache import listening_recipe
            sample = copy.deepcopy(session)
            recipe = listening_recipe(sample, 'transition', 1)
            note = capture_note(sample, 1, recipe['fingerprint'], {},
                                'Package test fixture; no human listening judgment.')
            sample.listening_notes.append(note)
            restored = Session.from_dict(json.loads(json.dumps(sample.to_dict())))
            require(restored.listening_notes == sample.listening_notes, 'Saved listening notes were lost.')
            require(note_status(note, restored) == 'current', 'Project reopening changed the reviewed sound identity.')
            restored.clips[1].bass_swap = not restored.clips[1].bass_swap
            require(note_status(note, restored) == 'changed', 'Changed blend sound retained current listening judgments.')
            require(all(v == 'unchecked' for v in note['judgments'].values()), 'Unchecked notes acquired judgments.')
            return {'project_version': sample.to_dict()['version'], 'roundtrip': True,
                    'changed_sound_recognized': True, 'implicit_judgments': False,
                    'native_dialog_imported': ListeningNotesDialog is not None,
                    'note': 'Tests provenance and persistence, not human listening quality.'}
        check('saved listening notes and audio identity', listening_notes)
        duration = djmix.probe(master)['duration']
        from studio_export import export_audio
        for preset in ('flac', 'mp3'):
            target = destination / f'Demo transition.{preset}'
            check(f'{preset} export', lambda p=preset, target=target: export_audio(master, target, p))
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance() or QCoreApplication([])
        for suffix in ('wav', 'flac', 'mp3'):
            path = destination / f'Demo transition.{suffix}'
            check(f'Qt {suffix} decoding', lambda path=path: qt_decode(path, duration))
        report['status'] = 'passed' if frozen else 'source-check-passed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        save()
        if frozen:
            os.chdir(original_cwd)
            if original_numba_cache is None: os.environ.pop('NUMBA_CACHE_DIR', None)
            else: os.environ['NUMBA_CACHE_DIR'] = original_numba_cache
    return report


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='A new directory for the report and test audio. Existing directories are refused.')
    parser.add_argument('--allow-source', action='store_true', help='Development check only; never reports a verified package.')
    args = parser.parse_args(arguments)
    try:
        report = run_checks(args.output, allow_source=args.allow_source)
    except OSError as error:
        if sys.stderr is not None:
            print(str(error), file=sys.stderr)
        return 2
    if sys.stdout is not None:
        print(f'{report["status"]}: {args.output.resolve() / "report.json"}')
    return 0 if report['status'] in ('passed', 'source-check-passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
