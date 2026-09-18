import json
import subprocess
from pathlib import Path

import yt_dlp

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "audio_cache"
PROCESSED_DIR = CACHE_DIR / "processed"
CACHE_DIR.mkdir(exist_ok=True)
PROCESSED_DIR.mkdir(exist_ok=True)

_FFMPEG = None

MAX_DURATION = 30  # segundos: alcanza para reconocer la canción y hace mucho más rápido a Demucs
CACHE_VERSION = 3  # subir al cambiar el pipeline (recorte, modelo Demucs, orden de stems, etc.)

_BAD_SUFFIX = {".part", ".ytdl", ".tmp", ".json"}
_BAD_SUBSTR = ("clean", "meta")


def log(msg: str):
    print(f"[audio] {msg}")


def have_ffmpeg() -> bool:
    global _FFMPEG
    if _FFMPEG is None:
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=15, check=True)
            _FFMPEG = True
            log("FFmpeg detectado.")
        except Exception:
            _FFMPEG = False
            log("FFmpeg NO detectado (la limpieza de audio y el modo Bandle lo requieren).")
    return _FFMPEG


def _files(key: str) -> list:
    targets = [f for f in CACHE_DIR.glob(f"{key}.*") if f.is_file()]
    clean_wav = CACHE_DIR / f"{key}_clean.wav"
    if clean_wav.is_file():
        targets.append(clean_wav)
    return targets


def find_raw(key: str) -> Path:
    """Archivo descargado por yt-dlp (puede tener cualquier extensión)."""
    for f in _files(key):
        if f.suffix.lower() in _BAD_SUFFIX:
            continue
        if any(b in f.name for b in _BAD_SUBSTR):
            continue
        return f
    return None


def _meta_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.meta.json"


def _load_meta(key: str) -> dict:
    try:
        return json.loads(_meta_path(key).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(key: str, meta: dict):
    try:
        _meta_path(key).write_text(json.dumps(meta), encoding="utf-8")
    except Exception as exc:
        log(f"No se pudo guardar metadata: {exc}")


def read_meta(key: str) -> dict:
    """Metadata pública de una canción (views, versión de pipeline, instrumentos, ...)."""
    return _load_meta(key)


def update_meta(key: str, **fields):
    """Actualiza campos de la metadata sin pisar el resto."""
    meta = _load_meta(key)
    meta.update(fields)
    _save_meta(key, meta)


def clean_audio(key: str) -> dict:
    """Devuelve {mp3, wav} con los archivos ya limpiados (si existen)."""
    out = {"mp3": None, "wav": None}
    wav = CACHE_DIR / f"{key}_clean.wav"
    mp3 = CACHE_DIR / f"{key}_clean.mp3"
    if wav.exists():
        out["wav"] = wav
    if mp3.exists():
        out["mp3"] = mp3
    return out


def has_clean_audio(key: str) -> bool:
    return bool(clean_audio(key)["wav"] or clean_audio(key)["mp3"])


def cleanup_intermediates(key: str):
    """Borra los intermedios pesados de una canción conservando `_clean.mp3` (y los stems).

    Si solo se conserva el MP3, Heardle reusa al instante y Bandle regenera desde cero si
    algún día se borran los stems.
    """
    removed = 0
    for f in _files(key):
        name = f.name
        if name.endswith("_clean.mp3") or name.endswith(".meta.json"):
            continue
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    if removed:
        log(f"Limpieza '{key}': {removed} archivo(s) intermedios eliminados.")


def cleanup_all_intermediates():
    """Barrido al arranque: recupera espacio de canciones ya procesadas.

    Las canciones cacheadas con una versión anterior del pipeline (la metadata
    guardada no coincide con CACHE_VERSION) se purgan para que se vuelvan a
    generar con el pipeline actual (recorte a 30 s, modelo Demucs, orden de stems).
    """
    keys = set()
    for f in CACHE_DIR.glob("*_clean.mp3"):
        keys.add(f.name[: -len("_clean.mp3")])
    if PROCESSED_DIR.exists():
        for d in PROCESSED_DIR.iterdir():
            if d.is_dir():
                keys.add(d.name)
    purged = 0
    for k in sorted(keys):
        if _load_meta(k).get("v") == CACHE_VERSION:
            cleanup_intermediates(k)
        else:
            purge_key(k)
            purged += 1
    log(f"Barrido de limpieza terminado ({len(keys)} canciones consideradas, {purged} purgadas para regenerar con el pipeline actual).")


def purge_key(key: str):
    """Borra todo el caché de una canción (incluido MP3/stems/meta) para re-generarla."""
    removed = 0
    for f in _files(key):
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    processed = PROCESSED_DIR / key
    if processed.is_dir():
        try:
            import shutil as _shutil

            _shutil.rmtree(processed, ignore_errors=True)
            removed += 1
        except Exception:
            pass
    if removed:
        log(f"Purga '{key}': caché vieja eliminada (se regenerará recortada a {MAX_DURATION} s).")


def download_and_clean(key: str, track: dict, need_wav: bool = False) -> dict:
    """Descarga buscando 'Tema Audio' (evita videoclips), aplica SponsorBlock
    (music_offtopic), recorta el silencio inicial y genera el MP3.

    - need_wav=True (Bandle): además conserva el WAV limpio para Demucs.
    - need_wav=False (Heardle): sólo se conserva el MP3 (los WAV intermedios se borran).

    Devuelve {"audio_url", "wav", "mp3", "views"}.
    """
    result = {"audio_url": None, "wav": None, "mp3": None, "views": None}
    meta = _load_meta(key)
    result["views"] = meta.get("views")

    cached = clean_audio(key)
    if need_wav and cached["wav"]:
        result["wav"] = cached["wav"]
        result["mp3"] = cached["mp3"]
        result["audio_url"] = (cached["mp3"] or cached["wav"]).name
        log(f"Cache reutilizado: {result['audio_url']}")
        return result
    if cached["mp3"]:
        result["mp3"] = cached["mp3"]
        result["audio_url"] = cached["mp3"].name
        if need_wav:
            for cand in (CACHE_DIR / f"{key}.wav", CACHE_DIR / f"{key}_clean.wav"):
                if cand.exists():
                    result["wav"] = cand
                    break
            if not result["wav"] and have_ffmpeg():
                # Re-generar el WAV limpio desde el MP3 cacheado (rápido: el MP3 ya es de 30 s).
                clean_wav = CACHE_DIR / f"{key}_clean.wav"
                rc = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(cached["mp3"]),
                     "-t", str(MAX_DURATION), "-acodec", "pcm_s16le", str(clean_wav)],
                    capture_output=True,
                    timeout=300,
                )
                if rc.returncode == 0 and clean_wav.exists():
                    result["wav"] = clean_wav
        log(f"Cache MP3 reutilizado: {result['audio_url']}")
        return result

    artist = (track.get("artist") or "").strip()
    name = (track.get("name") or "").strip()

    if not find_raw(key):
        ffmpeg = have_ffmpeg()
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(CACHE_DIR / f"{key}.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        if ffmpeg:
            opts.update(
                {
                    "sponsorblock_remove": ["music_offtopic"],
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "wav",
                            "nopostoverwrites": True,
                        }
                    ],
                    "postprocessor_args": ["-ac", "2", "-ar", "44100"],
                }
            )

        downloaded = False
        views = None
        for q in [f"{artist} {name} Audio", f"{artist} - {name}", f"{name} audio"]:
            try:
                # La búsqueda devuelve el video real (id + view_count); el "stub"
                # de ytsearch1 no incluye metadata completa.
                with yt_dlp.YoutubeDL(
                    {"quiet": True, "no_warnings": True, "noplaylist": True}
                ) as ydl:
                    results = ydl.extract_info(f"ytsearch3:{q}", download=False)
                candidates = [x for x in (results.get("entries") or []) if x.get("id")]
                if not candidates:
                    log(f"Sin resultados para '{q}'")
                    continue
            except Exception as exc:
                log(f"Búsqueda fallida '{q}': {exc}")
                continue

            for cand in candidates:
                url = f"https://www.youtube.com/watch?v={cand['id']}"
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                    views = cand.get("view_count") or (info or {}).get("view_count")
                    downloaded = True
                    break
                except Exception as exc:
                    log(f"No se pudo descargar '{url}': {exc}")
                    continue
            if downloaded:
                break

        if not downloaded:
            log("No se encontró audio en YouTube para esta canción.")
            return result
        try:
            _save_meta(key, {"views": views, "v": CACHE_VERSION})
            result["views"] = views
        except Exception:
            pass

    raw = find_raw(key)
    wav_extracted = CACHE_DIR / f"{key}.wav"

    if wav_extracted.exists():
        result["wav"] = wav_extracted
    elif raw and have_ffmpeg():
        rc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(raw), "-ac", "2", "-ar", "44100", str(wav_extracted)],
            capture_output=True,
            timeout=600,
        )
        if rc.returncode == 0 and wav_extracted.exists():
            result["wav"] = wav_extracted

    _trim_and_encode(key, result)

    # Heardle: solo conservamos el MP3. Los WAV intermedios (grandes) se borran.
    if not need_wav and result["mp3"]:
        cleanup_intermediates(key)
    return result


def _trim_and_encode(key: str, result: dict) -> dict:
    wav = result.get("wav")

    if wav and wav.exists():
        clean_wav = CACHE_DIR / f"{key}_clean.wav"
        if not clean_wav.exists():
            if not _trim_with_ffmpeg(wav, clean_wav):
                _trim_with_pydub(wav, clean_wav)
        if clean_wav.exists():
            result["wav"] = clean_wav

    if result.get("wav") and have_ffmpeg():
        mp3 = CACHE_DIR / f"{key}_clean.mp3"
        if not mp3.exists():
            rc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(result["wav"]),
                 "-t", str(MAX_DURATION),
                 "-codec:a", "libmp3lame", "-q:a", "4", str(mp3)],
                capture_output=True,
                timeout=600,
            )
            if rc.returncode == 0 and mp3.exists():
                result["mp3"] = mp3

    chosen = result.get("mp3") or result.get("wav")
    if chosen:
        result["audio_url"] = chosen.name
    else:
        raw = find_raw(key)
        if raw:
            result["audio_url"] = raw.name
    return result


def _trim_with_ffmpeg(src: Path, out: Path) -> bool:
    """Quita el silencio inicial (< -50 dB) y conserva solo los primeros 30 s."""
    if not have_ffmpeg():
        return False
    try:
        rc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src),
             "-af", "silenceremove=start_periods=1:start_threshold=-50dB:start_duration=0.3",
             "-t", str(MAX_DURATION),
             "-acodec", "pcm_s16le", str(out)],
            capture_output=True,
            timeout=600,
        )
        if rc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            log(f"Silencio inicial recortado con silenceremove: {src.name}")
            return True
    except Exception as exc:
        log(f"silenceremove fallido: {exc}")
    return False


def _trim_with_pydub(src: Path, out: Path) -> bool:
    """Fallback con pydub (requiere audioop/pyaudioop, ausente en Python 3.13+).
    También limita a los primeros 30 s."""
    try:
        from pydub import AudioSegment

        if not src.exists():
            return False
        seg = AudioSegment.from_file(str(src))
        nonsilent = seg.detect_nonsilent(
            min_silence_len=250, silence_thresh=-50.0, seek_step=10
        )
        start = max(0, (nonsilent[0][0] - 80)) if nonsilent else 0
        if start > 0:
            log(f"Silencio inicial recortado (pydub): {start} ms")
        seg[start:(start + MAX_DURATION * 1000)].export(str(out), format="wav")
        return out.exists()
    except Exception as exc:
        log(f"No se pudo recortar el silencio inicial (pydub): {exc}")
        return False