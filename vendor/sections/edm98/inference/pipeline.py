import importlib
import inspect
import json
import logging
import math
import os
import sys
import gc
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch

from edm98.constants import DEFAULT_DATA_DIR

from .labels import DATASET_LABEL_TO_DATASET_ID, build_label_mask
from .model import Model
from .postprocess import rule_post_processing


CONFIG_PATH = DEFAULT_DATA_DIR.parent / "configs" / "edmformer.yaml"
DEFAULT_CHECKPOINT_PATH = DEFAULT_DATA_DIR / "checkpoints" / "model.pt"
DEFAULT_MUSICFM_STAT_PATH = DEFAULT_DATA_DIR / "checkpoints" / "msd_stats.json"
DEFAULT_MUSICFM_MODEL_PATH = DEFAULT_DATA_DIR / "checkpoints" / "pretrained_msd.pt"
DEFAULT_HF_CACHE_DIR = DEFAULT_DATA_DIR.parent / ".cache" / "huggingface"

INPUT_SAMPLING_RATE = 24000
TIME_DUR = 420
AFTER_DOWNSAMPLING_FRAME_RATES = 8.333

LOGGER = logging.getLogger(__name__)


def _dict_to_namespace(data):
    if isinstance(data, dict):
        return SimpleNamespace(**{key: _dict_to_namespace(value) for key, value in data.items()})
    if isinstance(data, list):
        return [_dict_to_namespace(item) for item in data]
    return data


def load_config(config_path: str | Path = CONFIG_PATH):
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "Inference requires PyYAML. Install optional inference dependencies first."
        ) from exc

    path = Path(config_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _dict_to_namespace(data)


def load_checkpoint(checkpoint_path: str | Path, device=None):
    checkpoint_path = str(checkpoint_path)
    if device is None:
        device = "cpu"

    path = Path(checkpoint_path)
    if not path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
    if path.is_file():
        with path.open("rb") as handle:
            prefix = handle.read(64)
        if prefix.startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise RuntimeError(
                f"{checkpoint_path} is a Git LFS pointer, not the real binary. "
                "Fetch LFS assets before running inference."
            )

    if checkpoint_path.endswith(".pt"):
        return torch.load(checkpoint_path, map_location=device, weights_only=False)
    if checkpoint_path.endswith(".safetensors"):
        from safetensors.torch import load_file

        return {"model_ema": load_file(checkpoint_path, device=device)}
    raise ValueError("Unsupported checkpoint format. Use .pt or .safetensors")


def extract_model_state_dict(checkpoint: dict) -> dict:
    if checkpoint.get("model") is not None:
        return checkpoint["model"]

    model_ema = checkpoint.get("model_ema")
    if model_ema is None:
        raise RuntimeError("Checkpoint missing 'model' or 'model_ema' state.")

    cleaned = {}
    for key, value in model_ema.items():
        if key in {"initted", "step"}:
            continue
        if key.startswith("ema_model."):
            cleaned[key.removeprefix("ema_model.")] = value
        else:
            cleaned[key] = value
    return cleaned


def _load_audio_backend():
    try:
        import librosa
    except ImportError as exc:
        raise RuntimeError(
            "Inference requires librosa. Install optional inference dependencies first."
        ) from exc
    return librosa


def _resolve_device(requested: str | None) -> torch.device:
    requested = (requested or "auto").lower()
    if requested in {"cpu", "mps", "cuda"}:
        if requested == "cuda":
            try:
                _ = torch.zeros(1, device="cuda")
                return torch.device("cuda")
            except Exception as exc:
                raise RuntimeError(f"CUDA requested but unavailable: {exc}") from exc
        if requested == "mps":
            if not torch.backends.mps.is_available():
                raise RuntimeError("MPS requested but unavailable.")
            return torch.device("mps")
        return torch.device("cpu")

    try:
        _ = torch.zeros(1, device="cuda")
        return torch.device("cuda")
    except Exception as exc:
        LOGGER.warning("CUDA unavailable, falling back from auto device selection: %s", exc)

    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _configure_hf_cache(
    cache_dir: str | Path = DEFAULT_HF_CACHE_DIR,
    offline: bool = False,
    no_cache: bool = False,
) -> Path:
    if no_cache:
        cache_path = Path(tempfile.mkdtemp(prefix="edm98-hf-cache-"))
    else:
        cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    hub_path = cache_path / "hub"
    hub_path.mkdir(parents=True, exist_ok=True)

    os.environ["HF_HOME"] = str(cache_path)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hub_path)
    os.environ["TRANSFORMERS_CACHE"] = str(cache_path / "transformers")

    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)

    LOGGER.info("Using Hugging Face cache directory: %s", cache_path)
    if no_cache:
        LOGGER.info("Ephemeral no-cache mode enabled for Hugging Face-backed assets")
    if offline:
        LOGGER.info("Hugging Face local-only mode enabled")
    return cache_path


def _call_with_optional_kwargs(func, *args, **kwargs):
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None

    if signature is None:
        try:
            return func(*args, **kwargs)
        except TypeError:
            filtered = {key: value for key, value in kwargs.items() if key != "local_files_only"}
            return func(*args, **filtered)

    supported = {
        key: value for key, value in kwargs.items() if key in signature.parameters
    }
    return func(*args, **supported)


def _load_muq():
    try:
        from muq import MuQ
    except ImportError as exc:
        raise RuntimeError(
            "Inference requires muq. Install optional inference dependencies first."
        ) from exc
    return MuQ


def _load_musicfm():
    musicfm_env = os.environ.get("MUSICFMPATH")
    candidate_paths = []
    if musicfm_env:
        env_path = Path(musicfm_env).expanduser()
        candidate_paths.extend(
            [
                env_path,
                env_path.parent if env_path.name == "musicfm" else None,
            ]
        )
    third_party_root = DEFAULT_DATA_DIR.parent / "third_party"
    candidate_paths.extend(
        [
            third_party_root,
            third_party_root / "musicfm",
        ]
    )

    for candidate in candidate_paths:
        if candidate is None:
            continue
        candidate_str = str(candidate)
        if candidate.exists() and candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)

    try:
        module = importlib.import_module("musicfm.model.musicfm_25hz")
    except ImportError as exc:
        raise RuntimeError(
            "Inference requires the MusicFM source tree. "
            "Clone https://github.com/minzwon/musicfm and set MUSICFMPATH to either "
            "that checkout or its parent directory, then retry."
        ) from exc
    return module.MusicFM25Hz


@contextmanager
def _torch_load_weights_only_false():
    original_torch_load = torch.load

    def patched_torch_load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return original_torch_load(*args, **kwargs)

    torch.load = patched_torch_load
    try:
        yield
    finally:
        torch.load = original_torch_load


class InferencePipeline:
    def __init__(
        self,
        checkpoint_path: str | Path = DEFAULT_CHECKPOINT_PATH,
        config_path: str | Path = CONFIG_PATH,
        musicfm_stat_path: str | Path = DEFAULT_MUSICFM_STAT_PATH,
        musicfm_model_path: str | Path = DEFAULT_MUSICFM_MODEL_PATH,
        device: str | None = None,
        dataset_label: str = "EDMFormer",
        apply_rule_postprocessing: bool = True,
        low_memory: bool = False,
        persistent_models: bool = False,
        hf_cache_dir: str | Path = DEFAULT_HF_CACHE_DIR,
        offline: bool = False,
        no_cache: bool = False,
    ):
        self.device = _resolve_device(device)
        LOGGER.info("Using device: %s", self.device)
        self.hf_cache_dir = _configure_hf_cache(
            hf_cache_dir,
            offline=offline,
            no_cache=no_cache,
        )
        self.offline = offline
        self.no_cache = no_cache
        self.config = load_config(config_path)
        self.dataset_label = dataset_label
        self.apply_rule_postprocessing = apply_rule_postprocessing
        self.low_memory = low_memory
        self.persistent_models = persistent_models
        LOGGER.info("Loaded config from %s", config_path)
        self.musicfm_stat_path = str(musicfm_stat_path)
        self.musicfm_model_path = str(musicfm_model_path)
        self.muq_model = None
        self.musicfm_model = None

        if self.persistent_models or not self.low_memory:
            self.muq_model = self._create_muq_model()
            self.musicfm_model = self._create_musicfm_model()

        self.model = Model(self.config)
        LOGGER.info("Loading EDMFormer checkpoint from %s", checkpoint_path)
        ckpt = load_checkpoint(checkpoint_path=checkpoint_path, device=self.device)
        state_dict = extract_model_state_dict(ckpt)
        self.model.load_state_dict(state_dict, strict=True)
        self.model.to(self.device).eval()
        LOGGER.info("Loaded EDMFormer checkpoint")

        mask = build_label_mask(
            num_classes=self.config.num_classes,
            dataset_label=self.dataset_label,
        )
        self.label_mask = (
            torch.tensor(mask, dtype=torch.bool, device=self.device)
            .unsqueeze(0)
            .unsqueeze(0)
        )
        self.dataset_ids = torch.tensor(
            [DATASET_LABEL_TO_DATASET_ID[self.dataset_label]],
            dtype=torch.long,
            device=self.device,
        )

    def _create_muq_model(self):
        MuQ = _load_muq()
        LOGGER.info("Initializing MuQ model")
        model = _call_with_optional_kwargs(
            MuQ.from_pretrained,
            "OpenMuQ/MuQ-large-msd-iter",
            cache_dir=str(self.hf_cache_dir),
            local_files_only=self.offline,
        )
        model = model.to(self.device).eval()
        LOGGER.info("Initialized MuQ model")
        return model

    def _create_musicfm_model(self):
        MusicFM25Hz = _load_musicfm()
        LOGGER.info(
            "Initializing MusicFM model from %s and %s",
            self.musicfm_stat_path,
            self.musicfm_model_path,
        )
        with _torch_load_weights_only_false():
            model = MusicFM25Hz(
                is_flash=False,
                stat_path=self.musicfm_stat_path,
                model_path=self.musicfm_model_path,
            )
        model = model.to(self.device).eval()
        LOGGER.info("Initialized MusicFM model")
        return model

    def _release_feature_models(self):
        self.muq_model = None
        self.musicfm_model = None
        gc.collect()

    def _extract_muq_block(self, audio: torch.Tensor, start_sec: int):
        model = self.muq_model or self._create_muq_model()
        with torch.no_grad():
            start_idx = start_sec * INPUT_SAMPLING_RATE
            end_idx = min((start_sec + TIME_DUR) * INPUT_SAMPLING_RATE, audio.shape[-1])
            audio_seg = audio[start_idx:end_idx]
            muq_output = model(audio_seg.unsqueeze(0), output_hidden_states=True)
            muq_embd_420s = muq_output["hidden_states"][10].detach().cpu()
            del muq_output

            wrapped = []
            for idx_30s in range(start_sec, start_sec + TIME_DUR, 30):
                start_idx_30s = idx_30s * INPUT_SAMPLING_RATE
                end_idx_30s = min(
                    (idx_30s + 30) * INPUT_SAMPLING_RATE,
                    audio.shape[-1],
                    (start_sec + TIME_DUR) * INPUT_SAMPLING_RATE,
                )
                if start_idx_30s >= audio.shape[-1]:
                    break
                if end_idx_30s - start_idx_30s <= 1024:
                    continue
                hidden = model(
                    audio[start_idx_30s:end_idx_30s].unsqueeze(0),
                    output_hidden_states=True,
                )["hidden_states"][10]
                wrapped.append(hidden.detach().cpu())
                del hidden

        wrapped_30s = torch.concatenate(wrapped, dim=1)
        if self.low_memory and not self.persistent_models:
            del model
            self.muq_model = None
            gc.collect()
        return wrapped_30s, muq_embd_420s

    def _extract_musicfm_block(self, audio: torch.Tensor, start_sec: int):
        model = self.musicfm_model or self._create_musicfm_model()
        with torch.no_grad():
            start_idx = start_sec * INPUT_SAMPLING_RATE
            end_idx = min((start_sec + TIME_DUR) * INPUT_SAMPLING_RATE, audio.shape[-1])
            audio_seg = audio[start_idx:end_idx]
            _, hidden_states = model.get_predictions(audio_seg.unsqueeze(0))
            musicfm_embd_420s = hidden_states[10].detach().cpu()
            del hidden_states

            wrapped = []
            for idx_30s in range(start_sec, start_sec + TIME_DUR, 30):
                start_idx_30s = idx_30s * INPUT_SAMPLING_RATE
                end_idx_30s = min(
                    (idx_30s + 30) * INPUT_SAMPLING_RATE,
                    audio.shape[-1],
                    (start_sec + TIME_DUR) * INPUT_SAMPLING_RATE,
                )
                if start_idx_30s >= audio.shape[-1]:
                    break
                if end_idx_30s - start_idx_30s <= 1024:
                    continue
                hidden = model.get_predictions(
                    audio[start_idx_30s:end_idx_30s].unsqueeze(0)
                )[1][10]
                wrapped.append(hidden.detach().cpu())
                del hidden

        wrapped_30s = torch.concatenate(wrapped, dim=1)
        if self.low_memory and not self.persistent_models:
            del model
            self.musicfm_model = None
            gc.collect()
        return wrapped_30s, musicfm_embd_420s

    def predict_file(
        self,
        audio_path: str | Path,
        progress_callback=None,
    ) -> list[dict[str, float | str]]:
        def report(progress: float, desc: str):
            if progress_callback is not None:
                progress_callback(progress, desc=desc)

        LOGGER.info("Running prediction for %s", audio_path)
        report(0.05, "Loading audio")
        librosa = _load_audio_backend()
        wav, _sr = librosa.load(audio_path, sr=INPUT_SAMPLING_RATE)
        audio = torch.tensor(wav).to(self.device)
        LOGGER.info("Decoded audio with %d samples", audio.shape[0])

        total_len = ((audio.shape[0] // INPUT_SAMPLING_RATE) // TIME_DUR * TIME_DUR) + TIME_DUR
        total_frames = math.ceil(total_len * AFTER_DOWNSAMPLING_FRAME_RATES)

        logits = {
            "function_logits": np.zeros([total_frames, self.config.num_classes]),
            "boundary_logits": np.zeros([total_frames]),
        }
        logits_num = {
            "function_logits": np.zeros([total_frames, self.config.num_classes]),
            "boundary_logits": np.zeros([total_frames]),
        }

        lens = 0
        i = 0
        num_chunks = max(1, math.ceil(audio.shape[0] / (INPUT_SAMPLING_RATE * TIME_DUR)))

        with torch.no_grad():
            while True:
                start_idx = i * INPUT_SAMPLING_RATE
                end_idx = min((i + TIME_DUR) * INPUT_SAMPLING_RATE, audio.shape[-1])
                if start_idx >= audio.shape[-1]:
                    break
                if end_idx - start_idx <= 1024:
                    break

                chunk_idx = i // TIME_DUR
                base_progress = 0.12 + (0.76 * (chunk_idx / num_chunks))
                LOGGER.debug("Processing segment starting at %.2fs", i)
                report(base_progress, f"Extracting MuQ embeddings ({chunk_idx + 1}/{num_chunks})")
                wrapped_muq_embd_30s, muq_embd_420s = self._extract_muq_block(audio, i)
                report(
                    min(base_progress + 0.22 / num_chunks, 0.88),
                    f"Extracting MusicFM embeddings ({chunk_idx + 1}/{num_chunks})",
                )
                wrapped_musicfm_embd_30s, musicfm_embd_420s = self._extract_musicfm_block(audio, i)

                all_embds = [
                    wrapped_musicfm_embd_30s,
                    wrapped_muq_embd_30s,
                    musicfm_embd_420s,
                    muq_embd_420s,
                ]

                embd_lens = [x.shape[1] for x in all_embds]
                min_embd_len = min(embd_lens)
                max_embd_len = max(embd_lens)
                if abs(max_embd_len - min_embd_len) > 4:
                    raise ValueError(
                        f"Embedding shapes differ too much: {max_embd_len} vs {min_embd_len}"
                    )
                for idx in range(len(all_embds)):
                    all_embds[idx] = all_embds[idx][:, :min_embd_len, :].to(self.device)

                embd = torch.concatenate(all_embds, axis=-1)
                del wrapped_muq_embd_30s, muq_embd_420s, wrapped_musicfm_embd_30s, musicfm_embd_420s, all_embds
                gc.collect()

                report(
                    min(base_progress + 0.45 / num_chunks, 0.92),
                    f"Running EDMFormer ({chunk_idx + 1}/{num_chunks})",
                )
                _msa_info, chunk_logits = self.model.infer(
                    input_embeddings=embd,
                    dataset_ids=self.dataset_ids,
                    label_id_masks=self.label_mask,
                    with_logits=True,
                )

                start_frame = int(i * AFTER_DOWNSAMPLING_FRAME_RATES)
                end_frame = start_frame + min(
                    math.ceil(TIME_DUR * AFTER_DOWNSAMPLING_FRAME_RATES),
                    chunk_logits["boundary_logits"][0].shape[0],
                )

                logits["function_logits"][start_frame:end_frame, :] += (
                    chunk_logits["function_logits"][0].detach().cpu().numpy()
                )
                logits["boundary_logits"][start_frame:end_frame] = (
                    chunk_logits["boundary_logits"][0].detach().cpu().numpy()
                )
                logits_num["function_logits"][start_frame:end_frame, :] += 1
                logits_num["boundary_logits"][start_frame:end_frame] += 1
                lens += end_frame - start_frame
                del embd, chunk_logits
                gc.collect()
                i += TIME_DUR

        report(0.94, "Postprocessing segments")
        logits["function_logits"] /= logits_num["function_logits"]
        logits["boundary_logits"] /= logits_num["boundary_logits"]

        logits["function_logits"] = torch.from_numpy(logits["function_logits"][:lens]).unsqueeze(0)
        logits["boundary_logits"] = torch.from_numpy(logits["boundary_logits"][:lens]).unsqueeze(0)

        from .postprocess import postprocess_functional_structure

        msa_infer_output = postprocess_functional_structure(logits, self.config)
        if self.apply_rule_postprocessing:
            msa_infer_output = rule_post_processing(msa_infer_output)
        LOGGER.info("Prediction completed with %d segments", len(msa_infer_output) - 1)
        report(1.0, "Done")

        output = []
        for idx in range(len(msa_infer_output) - 1):
            output.append(
                {
                    "label": msa_infer_output[idx][1],
                    "start": float(msa_infer_output[idx][0]),
                    "end": float(msa_infer_output[idx + 1][0]),
                }
            )
        return output


def warm_model_cache(
    musicfm_stat_path: str | Path = DEFAULT_MUSICFM_STAT_PATH,
    musicfm_model_path: str | Path = DEFAULT_MUSICFM_MODEL_PATH,
    device: str | None = "cpu",
    hf_cache_dir: str | Path = DEFAULT_HF_CACHE_DIR,
    offline: bool = False,
    no_cache: bool = False,
):
    resolved_device = _resolve_device(device)
    cache_path = _configure_hf_cache(
        hf_cache_dir,
        offline=offline,
        no_cache=no_cache,
    )
    LOGGER.info("Warming inference model cache on device: %s", resolved_device)

    MuQ = _load_muq()
    _call_with_optional_kwargs(
        MuQ.from_pretrained,
        "OpenMuQ/MuQ-large-msd-iter",
        cache_dir=str(cache_path),
        local_files_only=offline,
    )

    MusicFM25Hz = _load_musicfm()
    with _torch_load_weights_only_false():
        MusicFM25Hz(
            is_flash=False,
            stat_path=str(musicfm_stat_path),
            model_path=str(musicfm_model_path),
        )
    LOGGER.info("Inference model cache is ready")


def create_pipeline(**kwargs) -> InferencePipeline:
    return InferencePipeline(**kwargs)


def predict_with_pipeline(
    pipeline: InferencePipeline,
    audio_path: str | Path,
):
    return pipeline.predict_file(audio_path)


def predict_file(audio_path: str | Path, **kwargs):
    pipeline = InferencePipeline(**kwargs)
    return pipeline.predict_file(audio_path)


def write_prediction_json(prediction, output_path: str | Path):
    output_path = Path(output_path)
    output_path.write_text(json.dumps(prediction, indent=2), encoding="utf-8")
