"""Distribution checks must not replace data or mistake system tools for bundled ones."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio_package_check import bundled_tool, run_checks


class MacBundleBuildTests(unittest.TestCase):
    SPEC = '''
exe = EXE('program', exclude_binaries=True, strip=False, upx=True)
coll = COLLECT(exe, binaries, datas, strip=False, upx=True, upx_exclude=[], name='BeatStitch')
app = BUNDLE(coll, name='BeatStitch.app', bundle_identifier='local.djstudio.app')
'''

    def test_app_receives_executable_and_all_resources_without_collecting_twice(self):
        from build_desktop import macos_bundle_only_spec
        with tempfile.TemporaryDirectory() as folder:
            spec = Path(folder) / 'app.spec'
            spec.write_text(self.SPEC)
            macos_bundle_only_spec(spec)
            binaries, datas = [('lib', 'lib.so', 'BINARY')], [('model', 'model.pt', 'DATA')]
            calls = []
            def record(*args, **kwargs):
                calls.append((args, kwargs))
                return object()
            namespace = {'EXE': record, 'BUNDLE': record, 'binaries': binaries, 'datas': datas}
            exec(compile(spec.read_text(), str(spec), 'exec'), namespace)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][0], (namespace['exe'], binaries, datas))
            self.assertEqual(calls[1][1]['bundle_identifier'], 'local.djstudio.app')
            self.assertEqual(calls[0][1]['upx_exclude'], [])

    def test_unexpected_spec_is_rejected_without_changing_it(self):
        from build_desktop import macos_bundle_only_spec
        variants = (self.SPEC + '\nother = coll\n', self.SPEC.replace('BUNDLE(coll,', 'BUNDLE(exe,'),
                    self.SPEC.replace("name='BeatStitch')", "name='BeatStitch', new_option=True)"))
        with tempfile.TemporaryDirectory() as folder:
            spec = Path(folder) / 'app.spec'
            for source in variants:
                spec.write_text(source)
                with self.assertRaises(ValueError):
                    macos_bundle_only_spec(spec)
                self.assertEqual(spec.read_text(), source)

    def test_macos_generates_spec_before_building_but_other_platforms_keep_onedir(self):
        from build_desktop import run_packager
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'build').mkdir()
            spec = root / 'build/BeatStitch.spec'
            options = ['--windowed', '--onedir', '--name', 'BeatStitch', 'desktop_app.py']
            for platform in ('darwin', 'win32', 'linux'):
                spec.write_text(self.SPEC)
                with patch('build_desktop.sys.platform', platform), patch('build_desktop.subprocess.run') as run:
                    run_packager(options, root, root / 'dist', {})
                commands = [call.args[0] for call in run.call_args_list]
                self.assertIn('--clean', commands[-1])
                if platform == 'darwin':
                    self.assertEqual(len(commands), 2)
                    self.assertIn('PyInstaller.utils.cliutils.makespec', commands[0])
                    self.assertEqual(commands[1][-1], str(spec))
                    self.assertNotIn('--onedir', commands[1])
                    self.assertNotIn('COLLECT', spec.read_text())
                else:
                    self.assertEqual(len(commands), 1)
                    self.assertEqual(commands[0][-len(options):], options)
                    self.assertEqual(spec.read_text(), self.SPEC)

    def test_windows_rubberband_sidecar_and_notices_stay_beside_the_bundled_tool(self):
        from build_desktop import audio_tool_options
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = root/'bin'; binary.mkdir()
            (binary/'sndfile.dll').write_bytes(b'fixture DLL')
            (root/'licenses').mkdir()
            with patch('build_desktop.djmix.tool_path', side_effect=lambda name: str(binary/(name+'.exe'))):
                with patch('build_desktop.sys.platform', 'win32'):
                    options = audio_tool_options()
                self.assertIn(str(binary/'sndfile.dll')+':bin', options)
                self.assertIn(str(root/'licenses')+':licenses/audio-tools', options)
                self.assertEqual(options.count('--add-binary'), 4)
                with patch('build_desktop.sys.platform', 'darwin'):
                    options = audio_tool_options()
                self.assertEqual(options.count('--add-binary'), 3)
                self.assertNotIn('--add-data', options)


class PackageCheckTests(unittest.TestCase):
    def test_missing_beat_model_cannot_pass_by_silently_using_the_opening_fallback(self):
        from studio_engine import Track
        from studio_package_check import check_tempo_import
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'song.wav'
            source.write_bytes(b'unchanged source')
            def fallback(track, cache):
                track.bpm = 128.
                track.status = 'Opening-passage tempo estimate · check BPM and cues'
            with patch('studio_engine.estimate_track', side_effect=fallback):
                with self.assertRaisesRegex(RuntimeError, 'fell back'):
                    check_tempo_import(Track('song', str(source), 'Song', 60.), Path(folder) / 'check')

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            sentinel = output / 'report.json'
            sentinel.write_text('keep this report')
            with self.assertRaises(FileExistsError):
                run_checks(output)
            self.assertEqual(sentinel.read_text(), 'keep this report')

    def test_source_run_is_not_reported_as_a_verified_package(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'check'
            with patch('studio_package_check.sys.frozen', False, create=True):
                result = run_checks(output)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('packaged application', result['error'])
            self.assertEqual(json.loads((output / 'report.json').read_text()), result)

    def test_external_tool_and_symlink_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'bundle'
            root.mkdir()
            external = Path(folder) / 'ffmpeg'
            external.touch()
            with patch('djmix.tool_path', return_value=str(external)):
                with self.assertRaisesRegex(RuntimeError, 'outside the package'):
                    bundled_tool('ffmpeg', root, True)
                self.assertEqual(bundled_tool('ffmpeg', root, False), external.resolve())
            link = root / 'ffmpeg'
            try:
                link.symlink_to(external)
            except OSError:
                return  # Windows may not permit symlink creation in this account.
            with patch('djmix.tool_path', return_value=str(link)):
                with self.assertRaisesRegex(RuntimeError, 'outside the package'):
                    bundled_tool('ffmpeg', root, True)

    def test_build_check_uses_finished_executable_outside_checkout(self):
        import subprocess
        from build_desktop import check_package
        with tempfile.TemporaryDirectory() as folder, patch('build_desktop.subprocess.run') as run:
            def finished(command, **kwargs):
                output = Path(command[2]); output.mkdir()
                (output / 'report.json').write_text(json.dumps({
                    'status': 'passed', 'frozen': True, 'platform': 'win32',
                    'checks': [{'status': 'passed'}]}))
                return subprocess.CompletedProcess(command, 0)
            run.side_effect = finished
            with patch('build_desktop.sys.platform', 'win32'):
                check_package(Path(folder))
            args, kwargs = run.call_args
            self.assertEqual(args[0][0], str(Path(folder).resolve() / 'BeatStitch/BeatStitch.exe'))
            self.assertEqual(args[0][1], '--check-package')
            self.assertEqual(kwargs['env']['PATH'], '')
            self.assertNotEqual(kwargs['cwd'], folder)
            self.assertEqual(kwargs['timeout'], 300)

    def test_missing_tool_records_a_failure_and_does_not_continue_rendering(self):
        with tempfile.TemporaryDirectory() as folder, patch('djmix.tool_path', return_value=None):
            output = Path(folder) / 'check'
            result = run_checks(output, allow_source=True)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['checks'][-1]['status'], 'failed')
            self.assertIn('Missing ffmpeg', result['error'])
            self.assertFalse((output / 'Tone source.wav').exists())

    def test_zero_exit_without_a_package_report_cannot_pass(self):
        import subprocess
        from build_desktop import check_package
        with tempfile.TemporaryDirectory() as folder, patch('build_desktop.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0)
            with self.assertRaisesRegex(SystemExit, 'did not produce'):
                check_package(folder)


if __name__ == '__main__':
    unittest.main()
