import hashlib
import os
import random
import re
import secrets
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import spotipy
import yt_dlp  # noqa: F401  (verificado por audio_utils)
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from spotipy.oauth2 import SpotifyOAuth

import audio_utils
import demucs_utils

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback")

if not CLIENT_ID or not CLIENT_SECRET:
    raise RuntimeError(
        "Faltan SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET. "
        "Copiá .env.example a .env y completalos (Secret lo encontrás en el dashboard de tu app)."
    )

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = audio_utils.CACHE_DIR

auth_manager = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope="playlist-read-private user-library-read",
    cache_path=str(BASE_DIR / ".spotify_oauth_token_cache"),
    open_browser=False,
)
sp = spotipy.Spotify(auth_manager=auth_manager)

app = FastAPI(title="Heardle / Bandle Local")

# Recupera espacio de descargas viejas (conserva MP3 y stems) al arrancar.
try:
    audio_utils.cleanup_all_intermediates()
except Exception as exc:
    print(f"[audio] Barrido de limpieza fallido: {exc}")
try:
    demucs_utils.migrate_legacy_stems()
except Exception as exc:
    print(f"[demucs] Migración fallida: {exc}")

# Estado en memoria
playlist_state = {"id": None, "name": "", "tracks": []}
rounds = {}  # round_id -> dict de la ronda
last_track_id = None
download_lock = threading.Lock()  # hoy el executor serializa; se mantiene por robustez

executor = ThreadPoolExecutor(max_workers=1)
# Cola de reproducción (modo Bandle con "Procesamiento previo"):
# `order` guarda las próximas canciones en orden de uso; `done_ids` marca cuáles
# ya están procesadas. Al consumir el frente se rellena un lugar al final.
buffer_state = {
    "enabled": False,
    "ahead": 2,
    "order": [],      # track dicts: frente = próxima a usar
    "processing": False,
    "errors": [],
}
done_ids = set()          # ids de canciones de la cola ya procesadas y listas
claimed_ids = set()       # ids que una ronda activa está usando (evita trabajo duplicado)
processed_keys = set()    # track ids ya procesados en esta sesión (audio limpio o stems)
# Orden de reproducción aleatorio: permutación de la playlist para la cola,
# así el próximo tema no se deduce del orden original ni de las canciones agrupadas
# por artista/álbum de la playlist.
game_order = []           # track dicts, mezclados al cargar la playlist
order_ptr = 0             # próxima posición no encolada en game_order


def reset_buffer():
    buffer_state["order"] = []
    buffer_state["processing"] = False
    buffer_state["errors"] = []
    buffer_state["enabled"] = False


def _set_library(name: str, tracks: list, pid: str | None = None):
    """Carga una lista de canciones como biblioteca actual (playlist o 'Me gusta')."""
    global order_ptr
    playlist_state["id"] = pid
    playlist_state["name"] = name
    playlist_state["tracks"] = tracks
    game_order[:] = tracks
    random.shuffle(game_order)
    order_ptr = 0
    processed_keys.clear()  # la cache de procesado es por canción y sobrevive en disco
    reset_buffer()
    done_ids.clear()
    claimed_ids.clear()


def _bandle_song_ready(track: dict) -> bool:
    return demucs_utils.separation_exists(track_key(track))

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/audio", StaticFiles(directory=str(CACHE_DIR)), name="audio")


class PlaylistRequest(BaseModel):
    playlist_id: str


class RoundRequest(BaseModel):
    mode: str = "heardle"
    buffer: bool = False


class GuessRequest(BaseModel):
    round_id: str
    name: str = ""
    artist: str = ""


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def extract_playlist_id(value: str) -> str:
    m = re.search(r"playlist[/:]([0-9A-Za-z]+)", value or "")
    if m:
        return m.group(1)
    value = (value or "").strip()
    if re.fullmatch(r"[0-9A-Za-z]{15,}", value):
        return value
    raise HTTPException(400, "No se pudo extraer un ID de playlist válido.")


def require_connected():
    token = sp.auth_manager.get_cached_token()
    if not token or not token.get("access_token"):
        raise HTTPException(
            401,
            "No hay sesión de Spotify. Navegá a /login o pulsá 'Conectar Spotify'.",
        )
    return token


def track_key(track: dict) -> str:
    return track["id"] or hashlib.sha1(
        f"{track['name']}|{track['artist']}".encode("utf-8")
    ).hexdigest()[:12]


def set_status(rid: str, status: str, message: str = ""):
    r = rounds.get(rid)
    if r:
        r["status"] = status
        r["message"] = message
        if status == "ready":
            r["ready_at"] = time.time()
        print(f"[round {rid[:6]}] {status}: {message}")


# ------------------------- Procesamiento en background -------------------------


def process_round(rid: str):
    r = rounds.get(rid)
    if not r:
        return
    key = r["key"]
    track = r["track"]
    need_wav = r["mode"] == "bandle"

    set_status(rid, "downloading", "Descargando audio de YouTube...")
    try:
        res = audio_utils.download_and_clean(key, track, need_wav=need_wav)
    except Exception as exc:
        set_status(rid, "error", f"Error al descargar: {exc}")
        return

    if not res["audio_url"]:
        set_status(rid, "error", "No se pudo encontrar/descargar el audio en YouTube.")
        return

    r["audio_url"] = res["audio_url"]
    r["wav"] = res.get("wav")
    r["views"] = res.get("views")
    processed_keys.add(track["id"])

    if r["mode"] == "heardle":
        set_status(rid, "ready", "Lista")
        return

    # ---- Modo Bandle: separación con Demucs ----
    if demucs_utils.separation_exists(key):
        r["stems_paths"] = {s: demucs_utils.stem_path(key, s) for s in demucs_utils.STEMS}
        r["stems"] = {s: f"/api/round/{rid}/stem/{s}" for s in demucs_utils.STEMS}
        stored = audio_utils.read_meta(key).get("instruments")
        r["instruments"] = stored or demucs_utils.default_instruments()
        set_status(rid, "ready", "Stems ya cacheados")
        return

    if not r["wav"]:
        set_status(
            rid,
            "error",
            "El modo Bandle requiere FFmpeg para generar el WAV y separar con Demucs. "
            "Instalá FFmpeg (ver README) y reiniciá el server.",
        )
        return

    set_status(rid, "separating", "Separando instrumentos con Demucs (puede tardar varios minutos)...")
    try:
        out = demucs_utils.separate_track(str(r["wav"]), key)
        r["stems"] = {s: f"/api/round/{rid}/stem/{s}" for s in demucs_utils.STEMS}
        r["stems_paths"] = out["paths"]
        r["instruments"] = out["instruments"]
        audio_utils.update_meta(key, instruments=r["instruments"])
    except Exception as exc:
        set_status(rid, "error", f"Error al separar con Demucs: {exc}")
        return

    # Separación exitosa: ya no hacen falta los WAV gigantes, solo el MP3 y los stems.
    audio_utils.cleanup_intermediates(key)
    set_status(rid, "ready", "Lista")


def buffer_task(track: dict, buffer_mode: str):
    if track["id"] in claimed_ids:
        return  # una ronda ya lo está usando/procesando
    # Sin mostrar el nombre de la canción: sería un spoiler del juego.
    buffer_state["processing"] = True
    try:
        key = track_key(track)
        need_wav = buffer_mode == "bandle"
        res = audio_utils.download_and_clean(key, track, need_wav=need_wav)
        if buffer_mode == "bandle":
            if demucs_utils.separation_exists(key):
                pass
            elif res.get("wav"):
                out = demucs_utils.separate_track(str(res["wav"]), key)
                audio_utils.update_meta(key, instruments=out["instruments"])
                audio_utils.cleanup_intermediates(key)
        done_ids.add(track["id"])
        processed_keys.add(track["id"])
    except Exception as exc:
        buffer_state["errors"].append(str(exc))
        buffer_state["errors"] = buffer_state["errors"][-5:]
        print(f"[buffer] error en '{track['name']}': {exc}")
    finally:
        buffer_state["processing"] = False
        ready_n = sum(1 for t in buffer_state["order"] if t["id"] in done_ids)
        print(f"[buffer] cola: {len(buffer_state['order'])} canciones ({ready_n} listas)")


def ensure_buffer(consumed_id: str, buffer_mode: str):
    """Rellena la cola hasta `ahead` canciones después de consumir `consumed_id`.

    El orden de reproducción es una permutación aleatoria de la playlist (`game_order`):
    primero se procesa la canción del round y, mientras se juega, se preparan las
    siguientes de la mezcla, sin seguir el orden original de la playlist.
    """
    global order_ptr
    tracks = playlist_state["tracks"]
    n = len(tracks)
    if n == 0 or not game_order:
        return
    buffer_state["enabled"] = True

    buffer_state["order"] = [t for t in buffer_state["order"] if t["id"] != consumed_id]
    if not buffer_state["order"]:
        # Cola vacía (primera ronda o nueva sesión): alinear el puntero justo
        # después de la canción que se acaba de elegir.
        consumed_pos = next(
            (i for i, t in enumerate(game_order) if t["id"] == consumed_id), None
        )
        order_ptr = (consumed_pos + 1) % n if consumed_pos is not None else random.randrange(n)

    occupied = {t["id"] for t in buffer_state["order"]} | claimed_ids
    guard = 0
    while len(buffer_state["order"]) < buffer_state["ahead"] and guard <= n:
        cand = game_order[order_ptr % n]
        order_ptr = (order_ptr + 1) % n
        guard += 1
        if cand["id"] in occupied:
            continue
        buffer_state["order"].append(cand)
        occupied.add(cand["id"])
        if _bandle_song_ready(cand):
            done_ids.add(cand["id"])
        else:
            executor.submit(buffer_task, cand, buffer_mode)


# ------------------------------- Rutas web ------------------------------------


@app.get("/")
def index():
    return FileResponse(str(BASE_DIR / "templates" / "index.html"))


@app.get("/login")
def login():
    return RedirectResponse(sp.auth_manager.get_authorize_url())


@app.get("/callback")
def callback(code: str = "", error: str = ""):
    if error:
        raise HTTPException(400, f"Spotify denegó la autorización: {error}")
    if not code:
        raise HTTPException(400, "Falta el código de autorización.")
    try:
        sp.auth_manager.get_access_token(code)
    except Exception as exc:
        raise HTTPException(400, f"No se pudo obtener el token: {exc}")
    return RedirectResponse("/")


@app.get("/api/status")
def status():
    token = sp.auth_manager.get_cached_token()
    return {
        "connected": bool(token and token.get("access_token")),
        "ffmpeg": audio_utils.have_ffmpeg(),
        "device": demucs_utils.device(),
        "cuda": demucs_utils.cuda_available(),
    }


@app.post("/api/playlist")
def load_playlist(req: PlaylistRequest):
    require_connected()
    pid = extract_playlist_id(req.playlist_id)
    try:
        pl = sp.playlist(pid, fields="name")
        tracks = []
        offset = 0
        while True:
            items = sp.playlist_items(
                pid,
                limit=50,
                offset=offset,
                fields="items(item(name,artists(name),id,album(release_date))),next",
            )
            for it in items.get("items", []):
                # Spotify renombró el campo 'track' -> 'item' (feb/2026).
                tr = it.get("item") or it.get("track")
                if not tr or tr.get("id") is None or not tr.get("name"):
                    continue
                artists = ", ".join(a["name"] for a in (tr.get("artists") or []))
                year = ""
                release = (tr.get("album") or {}).get("release_date") or ""
                if re.fullmatch(r"\d{4}", release[:4]):
                    year = release[:4]
                tracks.append({"id": tr["id"], "name": tr["name"], "artist": artists, "year": year})
            if items.get("next"):
                offset += 50
            else:
                break
    except HTTPException:
        raise
    except spotipy.exceptions.SpotifyException as exc:
        if exc.http_status == 403:
            raise HTTPException(
                400,
                "Spotify devuelve 403 para playlists que no son tuyas ni en las que "
                "colaborás (política de feb/2026 para apps en Development Mode). "
                "Probá con una playlist propia o creá una con las canciones que quieras.",
            )
        raise HTTPException(
            400, f"Spotify devolvió un error ({exc.http_status}) al acceder a la playlist."
        )
    except Exception:
        raise HTTPException(400, "No se pudo acceder a la playlist (¿el ID es válido?).")

    if not tracks:
        raise HTTPException(400, "La playlist no tiene canciones disponibles.")

    _set_library(pl.get("name", "Playlist"), tracks, pid)
    print(f"[spotify] Playlist cargada: '{pl.get('name')}' ({len(tracks)} canciones)")
    return {
        "name": playlist_state["name"],
        "count": len(tracks),
        "tracks": tracks,
    }


@app.post("/api/load-liked-songs")
def load_liked_songs():
    require_connected()
    tracks = []
    offset = 0
    try:
        while True:
            items = sp.current_user_saved_tracks(limit=50, offset=offset)
            if not items or not items.get("items"):
                break
            for it in items.get("items", []):
                # Mismo esquema que playlist_items: 'item' o 'track' según la versión de la API.
                tr = it.get("item") or it.get("track")
                if not tr or tr.get("id") is None or not tr.get("name"):
                    continue
                artists = ", ".join(a["name"] for a in (tr.get("artists") or []))
                year = ""
                release = (tr.get("album") or {}).get("release_date") or ""
                if re.fullmatch(r"\d{4}", release[:4]):
                    year = release[:4]
                tracks.append({"id": tr["id"], "name": tr["name"], "artist": artists, "year": year})
            if items.get("next"):
                offset += 50
            else:
                break
    except spotipy.exceptions.SpotifyException as exc:
        raise HTTPException(
            400,
            f"Spotify devolvió un error ({exc.http_status}) al leer tus canciones guardadas. "
            "Verificá que la app tenga el scope 'user-library-read' y que hayas autorizado el acceso.",
        )
    except Exception as exc:
        raise HTTPException(400, f"No se pudieron leer tus canciones guardadas: {exc}")

    if not tracks:
        raise HTTPException(400, "No hay canciones guardadas en 'Me gusta'.")

    _set_library("Mis canciones que me gustan", tracks)
    print(f"[spotify] Canciones que me gustan cargadas ({len(tracks)})")
    return {
        "name": playlist_state["name"],
        "count": len(tracks),
        "tracks": tracks,
    }


@app.post("/api/round")
def new_round(req: RoundRequest):
    global last_track_id
    require_connected()

    mode = (req.mode or "heardle").strip().lower()
    if mode not in ("heardle", "bandle"):
        raise HTTPException(400, "Modo inválido. Usá 'heardle' o 'bandle'.")

    tracks = playlist_state["tracks"]
    if not tracks:
        raise HTTPException(400, "Primero cargá una playlist con /api/playlist.")

    if mode == "bandle" and not audio_utils.have_ffmpeg():
        raise HTTPException(
            400,
            "El modo Bandle requiere FFmpeg (para WAV + separación con Demucs). "
            "Instalá FFmpeg (ver README).",
        )

    # Cola de reproducción: consumir el frente (en orden) en vez de buscar una nueva.
    track = None
    if mode == "bandle" and req.buffer and buffer_state["order"]:
        queued = buffer_state["order"].pop(0)
        if queued["id"] == last_track_id and buffer_state["order"]:
            buffer_state["order"].append(queued)
            queued = buffer_state["order"].pop(0)
        track = queued
        done_ids.discard(track["id"])
    if track is None:
        candidates = [t for t in tracks if t["id"] != last_track_id]
        track = random.choice(candidates or tracks)
    last_track_id = track["id"]
    key = track_key(track)

    round_id = secrets.token_hex(6)
    rounds[round_id] = {
        "id": round_id,
        "mode": mode,
        "track": track,
        "key": key,
        "status": "pending",
        "message": "Encolado...",
        "audio_url": None,
        "wav": None,
        "stems": None,
        "stems_paths": None,
        "instruments": None,
        "created": time.time(),
        "ready_at": None,
    }

    executor.submit(process_round, round_id)
    if mode == "bandle":
        if req.buffer:
            claimed_ids.add(track["id"])
            ensure_buffer(track["id"], mode)
        else:
            reset_buffer()

    print(f"[game] Ronda {round_id} [{mode}] '{track['name']}'")
    return {
        "round_id": round_id,
        "mode": mode,
        "track": track,
        "buffer": bool(req.buffer),
    }


@app.get("/api/round/{round_id}/status")
def round_status(round_id: str):
    r = rounds.get(round_id)
    if not r:
        raise HTTPException(404, "Ronda no encontrada.")
    return {
        "status": r["status"],
        "message": r["message"],
        "mode": r["mode"],
        "track": r["track"],
        "audio_url": r["audio_url"],
        "views": r.get("views"),
        "stems": r["stems"],
        "instruments": r.get("instruments"),
    }


@app.get("/api/round/{round_id}/stem/{stem}")
def round_stem(round_id: str, stem: str):
    r = rounds.get(round_id)
    if not r:
        raise HTTPException(404, "Ronda no encontrada.")
    if stem not in demucs_utils.STEMS:
        raise HTTPException(404, "Stem inválido.")
    if not r["stems_paths"]:
        raise HTTPException(409, "El stem todavía no está listo.")
    path = Path(r["stems_paths"][stem])
    if not path.exists():
        raise HTTPException(404, "Stem no encontrado en disco.")
    media_type = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(str(path), media_type=media_type)


@app.get("/api/buffer")
def buffer_status():
    order = buffer_state["order"]
    prepared = sum(1 for t in order if t["id"] in done_ids)
    return {
        "enabled": buffer_state["enabled"],
        "ahead": len(order),
        "prepared": prepared,
        "pending": len(order) - prepared,
        "processing": buffer_state["processing"],
        "errors": buffer_state["errors"][-3:],
    }


@app.post("/api/guess")
def guess(req: GuessRequest):
    r = rounds.get(req.round_id)
    if not r:
        raise HTTPException(404, "Ronda no encontrada.")
    answer = r["track"]
    correct = (
        normalize_text(req.name) == normalize_text(answer["name"])
        and normalize_text(req.artist) == normalize_text(answer["artist"])
    )
    return {"correct": correct}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)