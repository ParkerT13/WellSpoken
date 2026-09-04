from __future__ import annotations

_device: str | None = None


def get_device() -> str:
    """"cuda" if a CUDA GPU is available, else "cpu" - checked once and cached,
    since torch.cuda.is_available() does real device enumeration work. Shared
    by Kokoro and Chatterbox so they agree on where to run.

    faster-whisper deliberately does NOT use this - it runs on CTranslate2,
    a separate native library from torch with its own CUDA/cuDNN runtime
    requirement (CUDA 12 cuDNN 9.x). torch here is pinned to a CUDA 11.8
    build (chosen for driver compatibility - verified this GPU's driver only
    supports up to CUDA 12.2, ruling out torch's newer cu124/cu126 builds).
    Making both CUDA generations' DLLs resolvable in the same process was
    tried and broke Chatterbox outright (a hard crash: "Could not load
    symbol cudnnGetLibConfig") - Windows DLL search order let CTranslate2's
    newer pip-installed cuDNN shadow torch's own bundled one. faster-whisper
    already runs acceptably on CPU (int8 quantization), so it stays there
    rather than risk that conflict for a smaller win than Kokoro/Chatterbox's
    GPU speedup."""
    global _device
    if _device is None:
        import torch

        _device = "cuda" if torch.cuda.is_available() else "cpu"
    return _device


def warm_up_cuda_cudnn() -> None:
    """Force torch to actually load cuDNN (a plain CUDA tensor op doesn't -
    cuDNN is loaded lazily, only by a cuDNN-backed op like conv/rnn) as early
    as possible in process startup, before faster-whisper/CTranslate2 can be
    imported anywhere.

    This must run before `wellspoken.app` (or anything importing
    wellspoken.transcribe.whisper_engine) is imported. Verified concretely:
    merely importing faster_whisper before torch has touched cuDNN leaves
    every later torch cuDNN op (e.g. Kokoro's RNN layers) failing with
    "Could not load symbol cudnnGetLibConfig" - CTranslate2's own cuDNN
    loading on Windows appears to shadow torch's, but only if it gets there
    first. Once torch has already loaded its own cuDNN, importing
    faster_whisper afterward causes no conflict (faster-whisper itself stays
    on CPU regardless - see the note in get_device()). A no-op on a machine
    with no CUDA GPU, and cheap (a few hundred ms) when there is one."""
    import torch

    if not torch.cuda.is_available():
        return
    x = torch.randn(1, 3, 8, device="cuda")
    torch.nn.Conv1d(3, 3, 3).cuda()(x)
    torch.cuda.synchronize()
