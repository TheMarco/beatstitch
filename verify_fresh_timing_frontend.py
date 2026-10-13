"""Check the official reference frontend on already observed control audio."""
from pathlib import Path
import argparse
import zipfile

import numpy as np
import torch

import djmix
import studio_structure
import prototype_fresh_timing_reference as fresh
from studio_structure_features import decoded_audio, extract
from beat_this.inference import Audio2Frames
from beat_this.model.postprocessor import Postprocessor
from detector_pilot import atomic_json, file_hash


def verify(folder):
    folder = Path(folder).resolve()
    source = Path(__file__).resolve().parent / 'outputs/gtzan-timing-feasibility/disco.00000.wav'
    info = djmix.probe(source)
    threads = torch.get_num_threads(); torch.set_num_threads(4)
    try:
        runner = Audio2Frames(str(studio_structure.model_path()), device='cpu')
        with decoded_audio(source, info['duration'], folder, lambda _:None) as (raw,count):
            with torch.inference_mode():
                mel,rms = extract(raw,count,lambda y:runner.signal2spect(y,22050),lambda _:None)
        with zipfile.ZipFile(folder/'source-gtzan.npz') as archive:
            official = fresh.load_spect(archive,'gtzan_disco_00000/track.npy')
        proof = {'source':str(source),'source_sha256':file_hash(source),
                 'plan_sha256':file_hash(folder/'plan.json'), 'runner_sha256':file_hash(__file__),
                 'official_member':'gtzan_disco_00000/track.npy',
                 'app_shape':list(mel.shape),'official_shape':list(official.shape),
                 'original_duration':info['duration'],'official_duration_estimate':len(official)/50,
                 'official_dtype':'float16 expanded to float32','ui_promotion_allowed':False}
        if mel.shape == official.shape:
            delta = np.abs(official-mel); quant = mel.astype(np.float16).astype(np.float32)
            proof.update(feature_max_abs_delta=float(delta.max()),
                         feature_p99_abs_delta=float(np.percentile(delta,99)),
                         exact_after_float16_quantization=bool(np.array_equal(official,quant)),
                         quantized_max_abs_delta=float(np.max(abs(official-quant))))
            outputs=[]
            for features in (mel,official):
                with torch.inference_mode():
                    bl,dl=runner.spect2frames(torch.from_numpy(features));beats,down=Postprocessor()(bl,dl)
                outputs.append((beats,down))
            proof['event_consistency'] = {event:{str(t):fresh.score_events(outputs[0][i],outputs[1][i],t)
                                                for t in (.03,.07)}
                                          for i,event in enumerate(('beats','downbeats'))}
            proof['frontend_control_passed'] = (proof['quantized_max_abs_delta']<.03 and
                all(s['0.03']['f1']>=.98 for s in proof['event_consistency'].values()))
        else: proof['frontend_control_passed']=False
        proof['scope'] = ('One previously observed mono recording verifies frontend structure and quantization effects. '
                          'Official float16 inputs remain a proxy for the app float32 pipeline. '
                          'This control does not authorize fresh calibrated UI confidence.')
        atomic_json(folder/'frontend-proof.json',proof)
        return proof
    finally: torch.set_num_threads(threads)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('folder',type=Path)
    args=parser.parse_args();proof=verify(args.folder)
    print('Frontend control passed:',proof['frontend_control_passed'])
    raise SystemExit(int(not proof['frontend_control_passed']))
