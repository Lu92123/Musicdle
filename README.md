# Heardle & Bandle Local

Juego estilo "Heardle" que funciona 100% local con dos modos:

- **Heardle (tiempo):** se elige una canción al azar de tu playlist y se descarga su audio con
  `yt-dlp` bajo demanda. Tenés 5 intentos con fragmentos de 1s, 3s, 5s, 15s y 30s y una barra
  de progreso visual con las marcas de tiempo.
- **Bandle (instrumentos IA):** separa la canción con **Demucs `htdemucs_6s`** en batería / bajo /
  guitarra / piano / otros / voz y reproducís los stems sincronizados con la **Web Audio API**.
  Los slots se arman **dinámicamente** según la canción: se descartan las pistas mudas (RMS muy
  bajo), las activas se ordenan por su punto de entrada (y por volumen si empatan) y la **voz
  siempre ocupa el último lugar**. Por intento se agrega una pista en ese orden transparente.

El audio de cada canción se procesa automáticamente: se evita el videoclip (preferencia por
`{artista} {cancion} Audio` / canales Topic), se quitan intros con SponsorBlock
(`music_offtopic`) y se **recorta el silencio inicial** antes de la música.

## Estructura

```
main.py                  Backend FastAPI (Spotify + yt-dlp + Demucs)
audio_utils.py           Descarga, limpieza y metadata (SponsorBlock, silencio, views)
demucs_utils.py          Separación de stems con Demucs (CUDA si está disponible) + migración WAV→MP3
templates/index.html     Interfaz (panel de pistas, reproductor, slots)
static/style.css         Estilos
static/script.js         Lógica del juego (fragmentos, barra, Web Audio, controles)
requirements.txt         Dependencias
.env.example             Plantilla de credenciales
audio_cache/             Cache de audio / stems (se crea automáticamente)
```

## Requisitos

- Python 3.9 o superior (probado en 3.14).
- Una app creada en el [Dashboard de desarrolladores de Spotify](https://developer.spotify.com/dashboard) para obtener el `CLIENT_ID` y el `CLIENT_SECRET`. Se usa el flujo **OAuth (Authorization Code)** con autorización de usuario: Spotify ya no permite leer las canciones de una playlist con *Client Credentials*.
- En el dashboard, en **Settings** de tu app: agregá `http://127.0.0.1:8000/callback` como **Redirect URI** y activá **User authentication** (Web API).
- **ffmpeg (obligatorio).** Se usa para convertir a WAV estéreo 44.1 kHz, aplicar SponsorBlock vía yt-dlp y generar MP3. Sin ffmpeg el modo Bandle se rechaza (400) y el parloteo/heardle pierde la limpieza de intros.
- Conexión a internet (Spotify API + YouTube).
- Para el modo Bandle: **Demucs + PyTorch** (ver más abajo; soporta CUDA para ser mucho más rápido).

## Instalación

1. Crear y activar el entorno virtual y abrir una terminal en la carpeta del proyecto:

   ```bash
   # Windows (PowerShell):
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # macOS / Linux:
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Instalar dependencias básicas:

   ```bash
   pip install -r requirements.txt
   ```

3. Crear el archivo `.env` a partir de la plantilla y completarlo:

   ```bash
   # Windows
   copy .env.example .env
   # macOS / Linux
   cp .env.example .env
   ```

   ```
   SPOTIFY_CLIENT_ID=tu_client_id
   SPOTIFY_CLIENT_SECRET=tu_client_secret_aqui
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/callback
   ```
   El Redirect URI debe coincidir exactamente con el registrado en el dashboard.

4. **Instalar ffmpeg** (obligatorio para limpieza de audio y modo Bandle):

   ```bash
   # Windows: winget install Gyan.FFmpeg   (o: choco install ffmpeg)
   # Ubuntu/Debian: sudo apt install ffmpeg
   # macOS: brew install ffmpeg
   ```

5. **Instalar PyTorch y Demucs** (solo para el modo Bandle). Con CPU no se necesita configuración
   especial pero la separación tarda varios minutos por canción. Con **GPU NVIDIA (CUDA)** es
   mucho más rápido; instalá la variante CUDA correspondiente a tu driver:

   ```bash
   # CPU:
   pip install torch torchaudio

   # GPU NVIDIA con CUDA 12.x:
   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124

   # Luego:
   pip install demucs
   ```

   Verificá en `/api/status` o en el pie de la interfaz que PyTorch esté disponible y qué
   dispositivo se usa (cpu / cuda). Sin torch, el modo Bandle no está disponible.

6. (Primera vez) Abrí http://127.0.0.1:8000 y pulsá **Conectar con Spotify**: se abre una
   ventana de Spotify, autorizás y volvés a la app. El token se guarda en `.spotify_oauth_token_cache`.

## Ejecución

```bash
uvicorn main:app --reload
```

o simplemente:

```bash
python main.py
```

Abrí http://127.0.0.1:8000 en el navegador.

## Uso

1. Al abrir la app (estando conectado) se cargan automáticamente tus **canciones que me gustan** como biblioteca por defecto (botón "Usar mis canciones que me gustan"). Podés alternar y pegar la URL de una playlist **tuya o donde colabores** (ej: `https://open.spotify.com/playlist/0PwWK8iR5WJdVFxrHmbpf2`) o solo su ID, y pulsar **Cargar**. Solo se descarga metadata (títulos y artistas), no los audios.
   > Importante: desde feb/2026 Spotify **bloquea (403)** leer las canciones de playlists de terceros incluso públicas (apps en Development Mode). Para jugar con una playlist ajena, en la app de Spotify elegí "Agregar a playlist" y guardala en una playlist tuya/colaborada.
2. Elegí el **modo**:

   - **Heardle:** pulsá **Nueva Ronda**. El backend elige una canción al azar, descarga el audio (sin videoclip), recorta el silencio inicial y lo guarda en `audio_cache/`.
   - **Bandle:** elegí **Procesamiento inmediato** (solo esta canción) o **Procesamiento previo (buffer)**, que funciona como una **fila de reproducción**: la primera vez se procesa la canción de la ronda en el acto y, mientras jugás, se preparan las otras 2 en un **orden aleatorio** (al cargar la playlist se mezcla una permutación, así no se puede deducir qué sigue del orden original ni de canciones agrupadas por artista/álbum; el estado muestra "Cola: X de 2 listas"). Cada ronda posterior **consume el frente de la cola** (listo al instante, sin re-descargar) y se rellena un lugar al final, de modo que siempre hay una canción descargada esperando después de la actual. Cada canción separada queda cacheada en `audio_cache/processed/`. La barra de instrumentos muestra qué pistas suenan en cada intento.

3. **Play** reproduce el fragmento actual; en Heardle se pausa solo al límite del intento y la barra de 30s marca en 1s/3s/5s/15s/30s dónde te encontrás.
4. **SALTAR** pasa al siguiente tramo. **ADIVINA** envía tu respuesta (autocompletado con la metadata de la playlist).
5. El panel "Pistas opcionales" (con toggle) y las fichas de instrumentos revelan pistas; al agotar los 5 intentos (o acertar) se revela la canción con su **año y vistas de YouTube**.
6. **Nueva Ronda** elige otra canción; los audios y stems ya procesados se reutilizan desde la caché.

## Cómo funciona el procesamiento por canción

1. búsqueda en YouTube: se consultan los primeros resultados de `ytsearch3:{artista} {canción} Audio` (con fallbacks a `{artista} - {canción}` y `{canción} audio`), se elige el video real con su ID y sus **vistas** (`view_count`), y se descarga con `format=bestaudio/best`;
2. SponsorBlock: se saltan los segmentos `music_offtopic` de la mitad/partes no musicales;
3. conversión a WAV estéreo 44.1 kHz (ffmpeg), recorte del silencio inicial (< -50 dB) con el filtro `silenceremove` y **límite a los primeros 30 segundos** (alcanza para que se reconozca; hace que Demucs procese mucho más rápido);
4. se sirve MP3 (si ffmpeg está disponible) o el WAV limpio;
5. en modo Bandle, Demucs (`htdemucs_6s`) separa en `drums/bass/guitar/piano/other/vocals` codificados como **MP3** (~1-3 MB por stem) dentro de `audio_cache/processed/{track_key}/stems/`. Un análisis RMS/onset descarta pistas mudas y ordena la lista de instrumentos (guardada en el meta de la canción) que el frontend usa para armar los slots.

## Pistas opcionales y metadata

- Cada canción muestra su **año** (de `album.release_date` de Spotify) y las **vistas de YouTube**, junto al nombre en la revelación final.
- El panel "Pistas opcionales" (arriba) muestra año y vistas como una pista extra, con un toggle que persiste tu preferencia (`localStorage`). En Bandle, las **fichas** se arman con la cantidad y el orden reales de instrumentos de la canción (nadie ve `???????` hasta que se desbloquea cada una) y la **voz queda siempre al final**.
- El reproductor incluye controles de **mute, play/pausa y loop** además de la barra de progreso (con marcas 1/3/5/15/30 s en Heardle).
- El botón **SALTAR** pasa al siguiente tramo; **ADIVINA** envía tu respuesta (autocompletado limitado a las canciones de la playlist).

## Notas

- Cada canción se descarga/procesa una sola vez (la caché usa el ID de Spotify). El almacenamiento es mínimo por diseño: Heardle conserva solo el MP3 final y Bandle los 4 stems MP3 (~10-15 MB por canción vs ~160 MB en WAV). Al arrancar, el server limpia automáticamente los intermedios viejos, migra stems WAV heredados a MP3 y **purga el caché anterior al recorte de 30 s** (se regenera solo en la próxima ronda). Para limpiar a mano, borrá el contenido de `audio_cache/`.
- La descarga + separación de Bandle puede tardar unos minutos la primera vez (más en CPU); el frontend muestra el estado con polling.
- Todo corre en localhost; los datos de sesión (playlist y rondas) viven en memoria del proceso.
- El token de Spotify se renueva automáticamente; si caduca, volvé a pulsar **Conectar con Spotify**.