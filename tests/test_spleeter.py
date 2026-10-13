"""Pinned model publication, safe numeric conversion and waveform timing."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import numpy as np
import soundfile as sf
import torch

import download_separation_model as installer
from studio_separation_model import load_model
from studio_separation_worker import infer
import studio_spleeter_checkpoint as checkpoint
from studio_spleeter_checkpoint import read_tensors
from studio_spleeter_model import STEMS, separate_waveform


def digest(data):
    return hashlib.sha256(data).hexdigest()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root/'official.tar.gz'
        self.files = {'model.index': b'index', 'model.data-00000-of-00001': b'data'}
        self.output = b'converted numeric arrays'

    def package(self, entries=None):
        with tarfile.open(self.archive, 'w:gz') as file:
            for name, data in (entries or list(self.files.items())):
                member = tarfile.TarInfo(name); member.size = len(data)
                file.addfile(member, BytesIO(data))

    def settings(self):
        return patch.multiple(installer, ARCHIVE_BYTES=self.archive.stat().st_size,
                              ARCHIVE_SHA256=installer.sha256(self.archive),
                              MODEL_BYTES=len(self.output), MODEL_SHA256=digest(self.output),
                              FILES={name: (len(data), digest(data)) for name, data in self.files.items()})

    def test_verified_reuse_and_failed_conversion_preserve_previous_file(self):
        self.package(); destination = self.root/'models'; destination.mkdir()
        target = destination/installer.MODEL_FILE; target.write_bytes(b'previous file')
        with self.settings(), patch.object(installer, 'convert_checkpoint', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                installer.fetch_model(destination, archive=self.archive, progress=lambda _: None)
        self.assertEqual(target.read_bytes(), b'previous file')
        self.assertEqual(list(destination.iterdir()), [target])
        def convert(source, output):
            for name, data in self.files.items(): self.assertEqual((source/name).read_bytes(), data)
            output.write_bytes(self.output)
        with self.settings(), patch.object(installer, 'convert_checkpoint', side_effect=convert) as converter:
            installer.fetch_model(destination, archive=self.archive, progress=lambda _: None)
            installer.fetch_model(destination, archive=self.root/'missing', progress=lambda _: None)
            self.assertEqual(converter.call_count, 1)
        self.assertEqual(target.read_bytes(), self.output)

    def test_corrupt_archive_and_output_are_not_published(self):
        self.package(); destination = self.root/'models'
        with self.settings(), patch.object(installer, 'ARCHIVE_SHA256', '0'*64):
            with self.assertRaisesRegex(ValueError, 'archive failed'):
                installer.fetch_model(destination, archive=self.archive, progress=lambda _: None)
        with self.settings(), patch.object(installer, 'convert_checkpoint', side_effect=lambda a,b: b.write_bytes(b'wrong')):
            with self.assertRaisesRegex(ValueError, 'converted separation'):
                installer.fetch_model(destination, archive=self.archive, progress=lambda _: None)
        self.assertFalse((destination/installer.MODEL_FILE).exists())
        self.assertEqual(list(destination.iterdir()), [])

    def test_traversal_duplicates_and_numeric_corruption_are_rejected(self):
        for entries in ([('../escape', b'x')], list(self.files.items())+[('model.index', b'index')],
                        [('model.index', b'wrong')], [('model.index', b'index')]):
            with self.subTest(entries=entries):
                self.package(entries)
                destination = self.root/'extract'; destination.mkdir(exist_ok=True)
                for p in destination.iterdir(): p.unlink()
                with patch.object(installer, 'FILES', {n: (len(d),digest(d)) for n,d in self.files.items()}):
                    with self.assertRaises(ValueError): installer.extract_numeric(self.archive, destination)
        self.assertFalse((self.root/'escape').exists())

    def test_symlink_and_unpinned_checkpoints_never_load(self):
        actual=self.root/'arrays'; actual.write_bytes(b'not trusted')
        link=self.root/'link'; link.symlink_to(actual)
        with self.assertRaises(ValueError): load_model(link)
        with self.assertRaises(ValueError): load_model(actual)
        with self.assertRaises(ValueError): read_tensors(self.root)

    def test_numeric_conversion_has_same_identity_with_windows_zip_defaults(self):
        arrays = {'test': np.array([[1., 2.]], dtype='<f4')}
        buffer = BytesIO(); np.lib.format.write_array(buffer, arrays['test'], allow_pickle=False)
        expected = BytesIO()
        with zipfile.ZipFile(expected, 'w') as archive:
            entry = zipfile.ZipInfo('test.npy', date_time=(1980,1,1,0,0,0))
            entry.create_system = 3; entry.external_attr = 0o600 << 16
            archive.writestr(entry, buffer.getvalue())
        raw = expected.getvalue(); original = zipfile.ZipInfo
        class WindowsZipInfo(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.create_system = 0
        with patch.object(checkpoint, 'read_tensors', return_value=arrays), \
                patch.object(checkpoint, 'CONVERTED_BYTES', len(raw)), \
                patch.object(checkpoint, 'CONVERTED_SHA256', digest(raw)), \
                patch.object(checkpoint.zipfile, 'ZipInfo', WindowsZipInfo):
            destination = self.root/'converted.npz'
            checkpoint.convert_checkpoint(self.root, destination)
        self.assertEqual(destination.read_bytes(), raw)


class WaveformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): torch.set_num_threads(2)

    def models(self):
        return {name: (lambda magnitude: magnitude) for name in STEMS}

    def test_sum_preserves_stereo_length_timing_and_high_frequencies(self):
        frames = 10763
        time = np.arange(frames)/44100
        audio = np.column_stack((.2*np.sin(time*18000*2*np.pi), .2*np.sin(time*440*2*np.pi))).astype('float32')
        audio[100,0] += .5; audio[-17,1] += .3
        outputs = separate_waveform(audio, self.models())
        self.assertTrue(all(a.shape == audio.shape and np.isfinite(a).all() for a in outputs.values()))
        np.testing.assert_allclose(sum(outputs.values()), audio, atol=3e-7)
        np.testing.assert_allclose(outputs['vocals'], audio/4, atol=1e-7)
        silent=separate_waveform(np.zeros((317,2),dtype='float32'), self.models())
        self.assertTrue(all(np.count_nonzero(a)==0 for a in silent.values()))

    def test_bad_audio_and_configuration_fail_before_inference(self):
        for audio in (np.zeros((0,2)), np.zeros((3,1)), np.full((3,2),np.nan)):
            with self.assertRaises(ValueError): separate_waveform(audio, self.models())
        with self.assertRaises(ValueError): separate_waveform(np.zeros((3,2)),self.models(),bins=1000)

    def test_worker_keeps_requested_source_frames_and_rejects_invalid_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary); frames=48000
            audio=np.column_stack((np.sin(np.arange(frames)*.1),np.cos(np.arange(frames)*.2))).astype('float32')*.1
            sf.write(work/'decoded.wav',audio,48000,subtype='FLOAT')
            request={'models':str(work), 'start':11111, 'end':43123}
            (work/'request.json').write_text(json.dumps(request))
            with patch('studio_separation_model.load_model',return_value=self.models()): result=infer(work)
            self.assertEqual(result, {'frames':32012,'rate':48000,'channels':2})
            from scipy.signal import resample_poly
            expected=resample_poly(resample_poly(audio,147,160,axis=0),160,147,axis=0)[11111:43123]
            combined=sum(sf.read(work/(name+'.wav'),dtype='float32',always_2d=True)[0] for name in STEMS)
            np.testing.assert_allclose(combined,expected,atol=2e-7)
            for start,end in ((True,2),(0,240*48000+1),(-1,2),(3,3),(0,frames+1)):
                (work/'request.json').write_text(json.dumps(dict(request,start=start,end=end)))
                with self.assertRaises(ValueError): infer(work)


if __name__ == '__main__': unittest.main()
