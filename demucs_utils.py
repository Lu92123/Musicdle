import os
import subprocess

import soundfile as sf

STEMS = ["drums", "bass", "guitar", "piano", "other", "vocals"]
STEM_LABELS = {
    "drums": "Batería",
    "bass": "Bajo",
    "guitar": "Guitarra",
    "piano": "Piano",
    "other": "Otros / FX",
    "vocals": "Voz",
}
STEM_EXT = "mp3"
MODEL_NAME = "htdemucs_6s"
RMS_THRESHOLD = 0.004  # RMS por debajo de esto: pista ausente/prácticamente muda

_IMPORT_ERROR = None
_torch = None


def _lazy_import():
    global _torch, _IMPORT_ERROR
    if _IMPORT_ERROR is not None or _torch is not None:
        return
    try:
        import torch

        _torch = torch
    except Exception as exc:
        _IMPORT_ERROR = str(exc)


def available() -> bool:
    _lazy_import()
    return _torch is not None


def device() -> str:
    _lazy_import()
    if _torch is None:
        return "no disponible (instalar torch)"
    return "cuda" if _torch.cuda.is_available() else "cpu"


def cuda_available() -> bool:
    return device() == "cuda"


def _get_model():
    _lazy_import()
    if _torch is None:
        raise RuntimeError(
            f"PyTorch no está instalado ({_IMPORT_ERROR}). "
            "Instalá torch/torchaudio (ver README) y reiniciá el server."
        )
    from demucs.pretrained import get_model as _gm

    model = _gm(MODEL_NAME)
    model.to(_torch.device(device()))
    model.eval()
    return model


def stems_dir(track_key: str) -> str:
    return os.path.join("audio_cache", "processed", str(track_key), "stems")


def stem_path(track_key: str, stem: str) -> str:
    if stem not in STEMS:
        raise ValueError(f"Stem inválido: {stem}")
    return os.path.join(stems_dir(track_key), f"{stem}.{STEM_EXT}")


def separation_exists(track_key: str) -> bool:
    return all(os.path.exists(stem_path(track_key, s)) for s in STEMS)


def instruments(items) -> list:
    """Convierte una lista de keys de stems en [{id, label}] legibles para el frontend."""
    return [{"id": s, "label": STEM_LABELS.get(s, s)} for s in items]


def default_instruments() -> list:
    """Orden por defecto (sin análisis): resto de instrumentos + voz al final."""
    others = [s for s in STEMS if s != "vocals"]
    return instruments(others + ["vocals"])


def order_stems(metrics: dict) -> list:
    """A partir de {stem: {"rms", "onset"}} devuelve la lista ordenada de instrumentos:

    - descarta las pistas por debajo de RMS_THRESHOLD (no suenan en la canción);
    - ordena las activas por punto de entrada (onset) y, ante empate, por volumen;
    - fija 'vocals' siempre en el último lugar disponible.
    """
    active = [s for s in STEMS if metrics.get(s, {}).get("rms", 0.0) >= RMS_THRESHOLD]
    if not active:
        active = list(STEMS)
    has_vocals = "vocals" in active
    others = [s for s in active if s != "vocals"]
    others.sort(key=lambda s: (metrics[s]["onset"], -metrics[s]["rms"]))
    return instruments(others + (["vocals"] if has_vocals else []))


def _encode_to_mp3(src: str, out: str) -> bool:
    try:
        rc = subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-codec:a", "libmp3lame", "-q:a", "4", out],
            capture_output=True,
            timeout=600,
        )
        return rc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0
    except Exception as exc:
        print(f"[demucs] fallo al codificar a MP3: {exc}")
        return False


def migrate_legacy_stems():
    """Convierte stems WAV (versiones viejas) a MP3 y borra los WAV. Devuelve nº convertidos."""
    base = os.path.join("audio_cache", "processed")
    converted = 0
    if not os.path.isdir(base):
        return converted
    for track_dir in os.listdir(base):
        stems = os.path.join(base, track_dir, "stems")
        if not os.path.isdir(stems):
            continue
        for stem in STEMS:
            wav = os.path.join(stems, f"{stem}.wav")
            mp3 = os.path.join(stems, f"{stem}.mp3")
            if os.path.exists(wav) and not os.path.exists(mp3):
                if _encode_to_mp3(wav, mp3):
                    converted += 1
            if os.path.exists(mp3) and os.path.exists(wav):
                try:
                    os.unlink(wav)
                except OSError:
                    pass
    if converted:
        print(f"[demucs] Stems migrados WAV->MP3: {converted}")
    return converted


def _stem_metrics(sources: dict, sr: int) -> dict:
    """RMS promedio y punto de entrada (primera "explosión" de sonido, ventanas de 0.1 s)."""
    torch = _torch
    metrics = {}
    for name, src in sources.items():
        x = src.float()  # (canales, muestras)
        rms = float(x.pow(2).mean().sqrt().item())
        mono = x.mean(0)
        win = max(1, int(sr * 0.1))
        n = max(1, mono.numel() // win)
        head = mono[: n * win].view(n, win)
        wrms = head.pow(2).mean(1).sqrt()
        thr = max(0.05 * (wrms.max().item() if wrms.numel() else 0.0), 1e-4)
        idx = (wrms > thr).nonzero(as_tuple=False)
        onset = float(idx[0].item() * 0.1) if idx.numel() else float("inf")
        metrics[name] = {"rms": rms, "onset": onset}
    return metrics


def separate_track(wav_path: str, track_key: str) -> dict:
    """Separa en 6 stems (drums/bass/guitar/piano/other/vocals) con `htdemucs_6s`.

    Devuelve {"paths": {stem: ruta MP3}, "instruments": [{id, label} en orden]}.
    La lista de instrumentos sale de analizar RMS y punto de entrada de cada pista
    (ordena por entrada, descarta mudas, voz al final).
    """
    _lazy_import()
    if _torch is None:
        raise RuntimeError("PyTorch no está instalado (ver README).")
    from demucs.apply import apply_model

    dev = device()
    print(f"[demucs] Separando '{wav_path}' en dispositivo '{dev}'…")

    model = _get_model()
    model_sources = list(model.sources)
    wav, sr = _load_wav(wav_path)
    if wav.shape[0] == 1:  # mono -> estéreo
        wav = wav.repeat(2, 1)

    ref = wav.mean(0)
    wav = (wav - ref.mean()) / ref.std()
    tensor = apply_model(model, wav[None], device=_torch.device(dev), shifts=1, split=True, progress=True)[0]
    tensor = tensor * ref.std() + ref.mean()

    sources = {}
    for i, stem in enumerate(model_sources):
        if stem in STEMS:
            sources[stem] = tensor[i].detach().cpu()

    metrics = _stem_metrics(sources, sr)

    out_dir = stems_dir(track_key)
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for stem in STEMS:
        if stem not in sources:
            continue
        wav_tmp = os.path.join(out_dir, f"_tmp_{stem}.wav")
        p = stem_path(track_key, stem)
        _save_wav(wav_tmp, sources[stem], sr)
        try:
            _encode_to_mp3(wav_tmp, p)
        finally:
            if os.path.exists(wav_tmp):
                try:
                    os.unlink(wav_tmp)
                except OSError:
                    pass
        if not os.path.exists(p):
            raise RuntimeError(f"No se pudo generar el stem {stem} en {p}")
        paths[stem] = p

    ordered = order_stems(metrics)
    print(f"[demucs] Separación completada: {[x['label'] for x in ordered]}")
    return {"paths": paths, "instruments": ordered}


def _load_wav(path: str):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return _torch.from_numpy(data.T).clone(), sr  # (canales, muestras)


def _save_wav(path: str, wav, sr: int):
    sf.write(path, wav.numpy().T, sr, subtype="PCM_16")