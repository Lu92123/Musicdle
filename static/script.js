const HEARDLE_TIMES = [1, 3, 5, 15, 30];
const BAR_TOTAL = 30;
const STEM_LABELS = { drums: 'Batería', bass: 'Bajo', guitar: 'Guitarra', piano: 'Piano', other: 'Otros / FX', vocals: 'Voz' };
const INSTR_ICONS = { drums: '🥁', bass: '🎸', guitar: '🎸', piano: '🎹', other: '🎛️', vocals: '🎤' };
const DEFAULT_INSTRUMENTS = ['drums', 'bass', 'guitar', 'piano', 'other', 'vocals'].map((id) => ({ id, label: STEM_LABELS[id] }));
const MODE_SLOTS = {
  heardle: [
    { icon: '⏱', txt: '1 segundo', sub: 'Primera escucha' },
    { icon: '⏱', txt: '3 segundos' },
    { icon: '⏱', txt: '5 segundos' },
    { icon: '⏱', txt: '15 segundos' },
    { icon: '⏱', txt: '30 segundos' },
  ],
  bandle: [
    { icon: '🥁', txt: 'Tambores / Percusión', sub: 'Batería desbloqueada' },
    { icon: '🎸', txt: 'Bajo', sub: 'Se suma el bajo' },
    { icon: '🎛️', txt: 'Otros / FX', sub: 'Se suma el resto' },
    { icon: '🎹', txt: 'Instrumental', sub: 'Completo sin voz' },
    { icon: '🎤', txt: 'Voz', sub: 'Canción completa' },
  ],
};

const el = (id) => document.getElementById(id);

function instrumentIds(inst) {
  return inst.map((x) => x.id);
}

function activeInstruments() {
  if (round && round.instruments && round.instruments.length) return round.instruments;
  return DEFAULT_INSTRUMENTS;
}

function maskForAttempt() {
  const inst = activeInstruments();
  const n = inst.length;
  const count = (attempt + 1 >= n || attempt >= 4 || gameOver) ? n : attempt + 1;
  return inst.slice(0, count);
}

const playCta = el('btn-play');
const skipBtn = el('btn-skip');
const submitBtn = el('btn-submit');
const newRoundBtn = el('btn-new-round');
const loadBtn = el('btn-load');
const likedBtn = el('btn-liked');
const plInput = el('playlist-input');
const guessInput = el('guess-input');
const dropdown = el('dropdown');
const audio = el('player');
const btnMute = el('btn-mute');
const btnLoop = el('btn-loop');

const attemptLabel = el('attempt-label');
const timeLabel = el('time-label');
const slotsEl = el('slots');
const statusText = el('playlist-status');
const loadStatus = el('load-status');
const roundStatus = el('round-status');
const bufferStatus = el('buffer-status');
const revealBox = el('reveal-box');
const revealText = el('reveal-text');
const answerTitle = el('answer-title');
const revealMeta = el('reveal-meta');
const connectBox = el('connect-box');
const envInfo = el('env-info');
const deviceLabel = el('device-label');
const bandleOptions = el('bandle-options');
const stemToggles = el('stem-toggles');
const progressFill = el('progress-fill');
const progressExplored = el('progress-explored');
const progressMarks = el('progress-marks');
const hints = el('hints');
const hintYear = el('hint-year');
const hintViews = el('hint-views');
const hintMsg = el('hint-msg');

let tracks = [];
let mode = 'heardle';
let round = null;
let roundReady = false;
let roundViews = null;
let attempt = 0;
let gameOver = false;
let selectedGuess = null;
let pauseTimer = null;
let busy = false;
let connected = false;
let raf = null;
let loopOn = false;
let muted = false;
let showHints = localStorage.getItem('bandle_hints') !== '0';

// Web Audio (Bandle)
let audioCtx = null;
let masterGain = null;
let stemBuf = {};
let stemGain = {};
let stemSource = {};
let stemEnded = {};
let bandle = { started: false, playing: false, startPos: 0, bootCtx: 0, offset: 0, loop: false, total: 0, teardown: false };
let bufferTimer = null;

/* ----------------------- utilidades ----------------------- */

function norm(s) {
  return (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

function formatViews(n) {
  n = Number(n);
  if (!n || isNaN(n)) return '';
  const chop = (x) => {
    const s = (Math.round(x * 10) / 10).toString();
    return s.endsWith('.0') ? s.slice(0, -2) : s;
  };
  if (n >= 1e9) return chop(n / 1e9) + 'B';
  if (n >= 1e6) return chop(n / 1e6) + 'M';
  if (n >= 1e3) return chop(n / 1e3) + 'K';
  return String(n);
}

async function fetchJSON(url, opts = {}, timeout = 20000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(url, { ...opts, signal: ctrl.signal });
    clearTimeout(timer);
    return res;
  } catch (e) {
    clearTimeout(timer);
    throw e;
  }
}

/* ----------------------- Pistas opcionales ----------------------- */

function updateHints() {
  const year = round ? (round.track.year || '') : '';
  hintYear.innerHTML = year ? `Lanzado en <b>${year}</b>` : '';
  hintViews.innerHTML = roundViews ? `Vistas en YouTube <b>${formatViews(roundViews)}</b>` : '';
  applyHintsVisibility();
}

function applyHintsVisibility() {
  hints.classList.toggle('hidden', !showHints);
  localStorage.setItem('bandle_hints', showHints ? '1' : '0');
}

/* ----------------------- Slots de intentos ----------------------- */

function renderSlots() {
  let data;
  if (mode === 'bandle' && round && roundReady) {
    const inst = activeInstruments();
    data = inst.map((x) => ({ icon: INSTR_ICONS[x.id] || '🎵', txt: x.label }));
  } else {
    data = MODE_SLOTS[mode] || [];
  }
  const n = data.length;
  const revealedCount = gameOver ? n : (mode === 'heardle' ? attempt + 1 : maskForAttempt().length);
  slotsEl.innerHTML = '';
  for (let i = 0; i < n; i++) {
    const revealed = i < revealedCount;
    const slot = document.createElement('div');
    slot.className = 'slot';
    if (i < attempt || gameOver) slot.classList.add('done');
    if (i === attempt && !gameOver) slot.classList.add('current');
    if (!revealed) slot.classList.add('locked');

    const title = document.createElement('span');
    title.className = 'slot-num';
    title.textContent = i + 1;

    if (revealed) {
      const ic = document.createElement('span');
      ic.className = 'slot-ic';
      ic.textContent = data[i].icon;
      const tx = document.createElement('span');
      tx.className = 'slot-txt';
      tx.textContent = data[i].txt;
      if (data[i].sub) {
        const sm = document.createElement('small');
        sm.textContent = data[i].sub;
        tx.appendChild(sm);
      }
      slot.appendChild(title);
      slot.appendChild(ic);
      slot.appendChild(tx);
    } else {
      const blank = document.createElement('span');
      blank.className = 'slot-blank';
      blank.textContent = '???????';
      slot.appendChild(title);
      slot.appendChild(blank);
    }
    slotsEl.appendChild(slot);
  }
}

/* ----------------------- Barra de progreso ----------------------- */

function markLabel(bar) {
  const marks = [];
  HEARDLE_TIMES.forEach((t) => {
    const pct = (t / BAR_TOTAL) * 100;
    marks.push(`<div class="mark" style="left:${pct}%"></div>`);
    marks.push(`<div class="mark-label" style="left:${pct}%">${t}s</div>`);
  });
  bar.innerHTML = marks.join('');
}

function segmentEnd() {
  return gameOver ? Infinity : HEARDLE_TIMES[attempt];
}

function heardlePos() {
  return round ? (audio.currentTime || 0) : 0;
}

function bandlePos() {
  if (!audioCtx) return 0;
  if (bandle.started && bandle.playing) {
    return bandle.startPos + (audioCtx.currentTime - bandle.bootCtx);
  }
  return bandle.offset;
}

function updateHeardleBar() {
  if (mode !== 'heardle') return;
  const a = attempt;
  const segStart = a > 0 ? HEARDLE_TIMES[a - 1] : 0;
  const segEnd = HEARDLE_TIMES[a];
  const live = heardlePos();
  const pos = Math.min(live, segEnd);
  progressExplored.style.width = `${(segStart / BAR_TOTAL) * 100}%`;
  progressFill.style.width = `${Math.max(segStart / BAR_TOTAL, pos / BAR_TOTAL) * 100}%`;
  timeLabel.textContent = `${fmt(segStart)} – ${fmt(segEnd)}`;
}

function updateBandleBar() {
  if (mode !== 'bandle') return;
  const dur = bandle.total || 0;
  const pos = Math.min(bandlePos(), dur);
  progressExplored.style.width = '0%';
  progressFill.style.width = dur ? `${(pos / dur) * 100}%` : '0%';
  timeLabel.textContent = dur ? `${fmt(pos)} / ${fmt(dur)}` : '';
}

function updateBar() {
  if (mode === 'heardle') updateHeardleBar();
  else updateBandleBar();
}

function startBar() {
  stopBar();
  raf = requestAnimationFrame(tick);
}

function stopBar() {
  cancelAnimationFrame(raf);
}

function tick() {
  updateBar();
  raf = requestAnimationFrame(tick);
}

/* ----------------------- Reproductor: común ----------------------- */

function updatePlayIcon(playing) {
  el('ic-play').classList.toggle('hidden', playing);
  el('ic-pause').classList.toggle('hidden', !playing);
}

function updateMuteIcon() {
  el('ic-vol').classList.toggle('hidden', muted);
  el('ic-mute').classList.toggle('hidden', !muted);
}

function toggleLoop() {
  loopOn = !loopOn;
  btnLoop.classList.toggle('active', loopOn);
  if (mode === 'bandle') bandle.loop = loopOn;
  if (mode === 'heardle' && gameOver) audio.loop = loopOn;
}

function toggleMute() {
  muted = !muted;
  btnMute.classList.toggle('active', muted);
  if (mode === 'heardle') audio.muted = muted;
  else if (masterGain) masterGain.gain.value = muted ? 0 : 1;
  updateMuteIcon();
}

/* ----------------------- Heardle ----------------------- */

function heardlePlayPause() {
  clearTimeout(pauseTimer);
  if (gameOver) {
    audio.loop = loopOn;
    if (audio.paused) audio.play();
    else audio.pause();
    updatePlayIcon(!audio.paused);
    return;
  }
  if (audio.paused) {
    const end = HEARDLE_TIMES[attempt];
    let t = audio.currentTime || 0;
    if (t <= 0 || t >= end - 0.1) t = 0;
    audio.currentTime = t;
    audio.play();
    if (!loopOn) {
      pauseTimer = setTimeout(() => {
        audio.pause();
        stopBar();
        updateBar();
        updatePlayIcon(false);
      }, (end - t) * 1000);
    }
    startBar();
    updatePlayIcon(true);
  } else {
    audio.pause();
    stopBar();
    updatePlayIcon(false);
  }
}

function onHeardleTime() {
  if (mode !== 'heardle' || gameOver || !round) return;
  const end = HEARDLE_TIMES[attempt];
  if (audio.currentTime >= end) {
    if (loopOn) {
      audio.currentTime = 0;
      if (audio.paused) audio.play();
    } else {
      audio.pause();
      audio.currentTime = Math.max(0, end - 0.05);
      stopBar();
      updatePlayIcon(false);
    }
    updateBar();
  }
}

audio.addEventListener('timeupdate', onHeardleTime);

/* ----------------------- Bandle (Web Audio) ----------------------- */

async function loadBandleStems(roundId) {
  await closeBandle();
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  masterGain = audioCtx.createGain();
  masterGain.gain.value = muted ? 0 : 1;
  masterGain.connect(audioCtx.destination);
  stemBuf = {};
  stemGain = {};
  stemSource = {};
  stemEnded = {};
  const inst = activeInstruments();
  for (const x of inst) {
    const s = x.id;
    const resp = await fetch(`/api/round/${roundId}/stem/${s}`);
    if (!resp.ok) throw new Error(`Stem ${s} no disponible`);
    stemBuf[s] = await audioCtx.decodeAudioData(await resp.arrayBuffer());
    stemGain[s] = audioCtx.createGain();
    stemGain[s].gain.value = 0;
    stemGain[s].connect(masterGain);
  }
  bandle = { started: false, playing: false, startPos: 0, bootCtx: 0, offset: 0, loop: loopOn, total: stemBuf.drums.duration, teardown: false };
}

async function closeBandle() {
  if (audioCtx && audioCtx.state !== 'closed') {
    try { await audioCtx.close(); } catch (_) {}
  }
  audioCtx = null;
  masterGain = null;
  stemBuf = {};
  stemGain = {};
  stemSource = {};
  stemEnded = {};
  bandle = { started: false, playing: false, startPos: 0, bootCtx: 0, offset: 0, loop: loopOn, total: 0, teardown: false };
}

function setBandleGains() {
  if (!audioCtx || gameOver) return;
  const ids = instrumentIds(maskForAttempt());
  activeInstruments().forEach((x) => {
    if (stemGain[x.id]) stemGain[x.id].gain.value = ids.includes(x.id) ? 1 : 0;
  });
}

function startSources(offset) {
  if (!audioCtx) return;
  const t0 = audioCtx.currentTime + 0.02;
  stemEnded = {};
  bandle.teardown = false;
  bandle.startPos = offset;
  bandle.bootCtx = t0;
  bandle.started = true;
  bandle.playing = true;
  const ids = instrumentIds(activeInstruments());
  for (const s of ids) {
    const src = audioCtx.createBufferSource();
    src.buffer = stemBuf[s];
    src.connect(stemGain[s]);
    src.onended = () => {
      stemEnded[s] = true;
      if (ids.every((x) => stemEnded[x])) onStemsEnded();
    };
    src.start(t0, offset);
    stemSource[s] = src;
  }
}

function stopSources() {
  bandle.teardown = true;
  const ids = instrumentIds(activeInstruments());
  for (const s of ids) {
    if (stemSource[s]) {
      try { stemSource[s].stop(); } catch (_) {}
      try { stemSource[s].disconnect(); } catch (_) {}
      stemSource[s] = null;
    }
  }
  bandle.started = false;
  bandle.playing = false;
}

function onStemsEnded() {
  if (bandle.teardown) return;
  if (bandle.loop && round && audioCtx) {
    startSources(0);
  } else {
    bandle.started = false;
    bandle.playing = false;
    bandle.offset = 0;
    updatePlayIcon(false);
  }
}

async function playBandle() {
  if (!audioCtx || !round || !roundReady) return;
  if (audioCtx.state === 'suspended') await audioCtx.resume();
  if (!bandle.started) {
    startSources(bandle.offset || 0);
    updatePlayIcon(true);
    startBar();
  } else if (bandle.playing) {
    bandle.offset = bandlePos();
    await audioCtx.suspend();
    bandle.playing = false;
    updatePlayIcon(false);
    stopBar();
    updateBar();
  } else {
    // Al reanudar, continuá desde donde se pausó: el audio ya estaba en bandle.offset
    // y la barra debe seguir ahí (no volver a startPos).
    bandle.startPos = bandle.offset;
    bandle.bootCtx = audioCtx.currentTime;
    await audioCtx.resume();
    bandle.playing = true;
    updatePlayIcon(true);
    startBar();
  }
}

/* ----------------------- Flujo de juego ----------------------- */

function renderState() {
  renderSlots();
  if (mode === 'heardle') {
    attemptLabel.textContent = round
      ? `Intento ${attempt + 1} de 5 · Escuchás ${HEARDLE_TIMES[attempt]} segundos`
      : '';
  } else {
    const ids = gameOver ? instrumentIds(activeInstruments()) : instrumentIds(maskForAttempt());
    attemptLabel.textContent = round
      ? `Intento ${attempt + 1} de 5 · Suenan: ${ids.map((id) => STEM_LABELS[id] || id).join(', ')}`
      : '';
    setBandleGains();
  }
  updateBar();
  updateButtons();
}

function updateButtons() {
  const canPlay = !!round && roundReady && !busy;
  const ctrlOk = canPlay || (gameOver && !!round);
  playCta.disabled = !canPlay && !(gameOver && mode === 'bandle' && !!round);
  skipBtn.disabled = !(canPlay && !gameOver);
  submitBtn.disabled = !(canPlay && !gameOver && selectedGuess);
  newRoundBtn.disabled = !connected || tracks.length === 0 || busy;
  guessInput.disabled = !(canPlay && !gameOver);
  loadBtn.disabled = !connected || busy;
  likedBtn.disabled = !connected || busy;
  plInput.disabled = !connected;
  btnMute.disabled = !ctrlOk;
  btnLoop.disabled = !ctrlOk;
}

function advanceAttempt() {
  clearTimeout(pauseTimer);
  stopBar();
  if (mode === 'heardle') {
    audio.pause();
    updatePlayIcon(false);
  }
  attempt++;
  renderState();
}

function skip() {
  if (gameOver) return;
  if (attempt < 4) advanceAttempt();
  else endGame(false);
}

/* ----------------------- Adivinar ----------------------- */

async function submitGuess() {
  if (!selectedGuess || !round || busy) return;
  busy = true;
  updateButtons();
  try {
    const res = await fetchJSON('/api/guess', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        round_id: round.round_id,
        name: selectedGuess.name,
        artist: selectedGuess.artist,
      }),
    });
    const data = await res.json();
    busy = false;
    if (data.correct) {
      endGame(true);
    } else if (attempt < 4) {
      advanceAttempt();
      guessInput.select();
    } else {
      endGame(false);
    }
  } catch (_) {
    busy = false;
    roundStatus.textContent = 'No se pudo enviar la respuesta.';
  }
  updateButtons();
}

function endGame(won) {
  gameOver = true;
  clearTimeout(pauseTimer);
  stopBar();
  if (mode === 'heardle') {
    audio.loop = loopOn;
    audio.setAttribute('controls', '');
    audio.currentTime = 0;
    audio.play();
    updatePlayIcon(true);
  } else {
    const inst = activeInstruments();
    inst.forEach((x) => {
      if (stemGain[x.id]) stemGain[x.id].gain.value = 1;
    });
    buildStemToggles(inst);
    stemToggles.classList.remove('hidden');
    if (audioCtx && !bandle.started) {
      startSources(0);
      updatePlayIcon(true);
      startBar();
    }
  }
  if (round && round.track) {
    answerTitle.textContent = `${round.track.name} — ${round.track.artist}`;
    const y = round.track.year || '';
    revealMeta.textContent = [y ? `Lanzado en ${y}` : '', roundViews ? `Vistas: ${formatViews(roundViews)}` : '']
      .filter(Boolean).join(' · ');
  }
  revealText.textContent = won
    ? `¡Correcto en ${attempt + 1} ${attempt === 0 ? 'intento' : 'intentos'}!`
    : 'Se acabaron los intentos. La canción era:';
  revealBox.classList.remove('hidden');
  roundStatus.textContent = 'Reproducción completa habilitada.';
  renderState();
}

/* ----------------------- Ronda ----------------------- */

async function waitRoundReady(roundId) {
  for (;;) {
    const res = await fetch(`/api/round/${roundId}/status`);
    const s = await res.json();
    if (s.status === 'ready') return s;
    if (s.status === 'error') {
      roundStatus.textContent = '';
      throw new Error(s.message || 'Error al procesar la ronda.');
    }
    roundStatus.textContent = s.message +
      (s.status === 'separating' ? ' (Esto puede tardar varios minutos la primera vez)' : '');
    await sleep(1500);
  }
}

async function newRound() {
  if (!tracks.length || busy) return;
  busy = true;
  clearTimeout(pauseTimer);
  stopBar();
  await closeBandle();
  stopBufferPoll();
  audio.pause();
  audio.removeAttribute('controls');
  selectedGuess = null;
  guessInput.value = '';
  revealBox.classList.add('hidden');
  stemToggles.classList.add('hidden');
  attempt = 0;
  gameOver = false;
  round = null;
  roundReady = false;
  roundViews = null;
  updatePlayIcon(false);
  renderState();
  updateHints();

  const buffer = mode === 'bandle' &&
    document.querySelector('input[name="buffer"]:checked').value === 'true';

  roundStatus.textContent = buffer
    ? 'Descargando y separando esta y las 2 siguientes canciones (puede tardar)...'
    : 'Descargando audio...';
  try {
    const res = await fetchJSON('/api/round', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, buffer }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Error al iniciar la ronda.');
    }
    round = await res.json();
    updateHints();
    const ready = await waitRoundReady(round.round_id);
    roundReady = true;
    roundViews = ready.views || null;
    round.instruments = ready.instruments || null;
    roundStatus.textContent = '';
    updateHints();
    if (mode === 'heardle') {
      audio.src = '/audio/' + ready.audio_url;
      audio.load();
    } else {
      await loadBandleStems(round.round_id);
      if (buffer) startBufferPoll();
    }
    renderState();
  } catch (e) {
    roundStatus.textContent = `Error: ${e.message}`;
    round = null;
    roundReady = false;
    updateHints();
  }
  busy = false;
  updateButtons();
  if (round) guessInput.focus();
}

/* ----------------------- Buffer ----------------------- */

function startBufferPoll() {
  stopBufferPoll();
  bufferTimer = setInterval(pollBuffer, 4000);
}
function stopBufferPoll() {
  if (bufferTimer) { clearInterval(bufferTimer); bufferTimer = null; }
}
async function pollBuffer() {
  try {
    const res = await fetch('/api/buffer');
    const b = await res.json();
    if (b.ahead > 0) {
      const preparing = b.pending > 0 && b.processing
        ? ' · Preparando la próxima...'
        : '';
      bufferStatus.textContent = b.pending > 0
        ? `Cola de reproducción: ${b.prepared} de ${b.ahead} listas${preparing}`
        : `Cola de reproducción: ${b.prepared} de ${b.ahead} listas · la próxima se usa automáticamente`;
    } else {
      bufferStatus.textContent = '';
    }
  } catch (_) {}
}

/* ----------------------- Autocompletado ----------------------- */

function filteredTracks(q) {
  const normName = (t) => norm(t.name);
  const normArtist = (t) => norm(t.artist);
  return tracks
    .filter((t) => normName(t).includes(q) || normArtist(t).includes(q))
    .sort((a, b) => {
      const aS = normName(a).startsWith(q) ? 0 : 1;
      const bS = normName(b).startsWith(q) ? 0 : 1;
      return aS - bS || normName(a).localeCompare(normName(b));
    });
}

function openDropdown(list) {
  dropdown.innerHTML = '';
  dropdown.classList.add('open');
  if (!list.length) {
    dropdown.innerHTML = '<div class="dd-empty">Sin coincidencias en la playlist</div>';
    return;
  }
  list.slice(0, 20).forEach((t) => {
    const div = document.createElement('div');
    div.className = 'dd-item';
    div.innerHTML = `<strong>${escapeHtml(t.name)}</strong><span>${escapeHtml(t.artist)}</span>`;
    div.addEventListener('mousedown', (e) => {
      e.preventDefault();
      guessInput.value = t.name;
      selectedGuess = t;
      dropdown.classList.remove('open');
      guessInput.blur();
      updateButtons();
    });
    dropdown.appendChild(div);
  });
}

function closeDropdown() { dropdown.classList.remove('open'); }

guessInput.addEventListener('input', () => {
  const q = norm(guessInput.value);
  openDropdown(filteredTracks(q));
  selectedGuess = tracks.find((t) => norm(t.name) === q) || null;
  updateButtons();
});

guessInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    if (selectedGuess) submitGuess();
    else if (dropdown.querySelector('.dd-item strong')) {
      const first = dropdown.querySelector('.dd-item strong').textContent;
      const t = tracks.find((x) => x.name === first);
      if (t) {
        guessInput.value = t.name;
        selectedGuess = t;
        closeDropdown();
        updateButtons();
      }
    }
  } else if (e.key === 'Escape') closeDropdown();
});

guessInput.addEventListener('focus', () => {
  if (!round || gameOver) return;
  openDropdown(filteredTracks(norm(guessInput.value)));
});

document.addEventListener('click', (e) => {
  if (!dropdown.contains(e.target) && e.target !== guessInput) closeDropdown();
});

/* ----------------------- Playlist & Conexión ----------------------- */

function applyLibrary(data, label) {
  tracks = data.tracks || [];
  statusText.textContent =
    `${label} "${data.name}" cargada (${data.count} canciones). Elegí el modo y pulsa "Nueva Ronda".`;
  loadStatus.textContent = 'Listo.';
  if (round) {
    closeBandle();
    audio.pause();
    audio.removeAttribute('controls');
    round = null;
    roundReady = false;
    revealBox.classList.add('hidden');
  }
  renderState();
}

async function loadLikedSongs() {
  likedBtn.disabled = true;
  loadStatus.textContent = 'Obteniendo tu biblioteca de "Me gusta"...';
  statusText.textContent = '';
  try {
    const res = await fetchJSON('/api/load-liked-songs', { method: 'POST' }, 60000);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (res.status === 401) connected = false;
      throw new Error(err.detail || 'Error al cargar tus canciones guardadas.');
    }
    const data = await res.json();
    applyLibrary(data, 'Biblioteca');
  } catch (e) {
    loadStatus.textContent = `Error: ${e.message}`;
  }
  likedBtn.disabled = false;
  updateButtons();
}

async function loadPlaylist() {
  const value = plInput.value.trim();
  if (!value) return;
  loadBtn.disabled = true;
  loadStatus.textContent = 'Obteniendo metadata de la playlist...';
  statusText.textContent = '';
  try {
    const res = await fetchJSON('/api/playlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: value }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (res.status === 401) connected = false;
      throw new Error(err.detail || 'Error al cargar la playlist.');
    }
    const data = await res.json();
    applyLibrary(data, 'Playlist');
  } catch (e) {
    loadStatus.textContent = `Error: ${e.message}`;
  }
  loadBtn.disabled = false;
  updateButtons();
}

async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    connected = !!data.connected;
    envInfo.textContent =
      `ffmpeg: ${data.ffmpeg ? 'sí' : 'no'} · PyTorch: ${data.device}` +
      (data.cuda ? ' (CUDA)' : '');
    deviceLabel.textContent = `dispositivo: ${data.device}${data.cuda ? ' (CUDA)' : ''}`;
  } catch (_) {
    connected = false;
  }
  connectBox.classList.toggle('hidden', connected);
  if (!connected) {
    statusText.textContent = 'Conectate con Spotify para cargar tu biblioteca.';
    loadStatus.textContent = '';
  } else if (tracks.length === 0) {
    loadLikedSongs();
  }
  updateButtons();
}

/* ----------------------- Modo ----------------------- */

function setMode(m) {
  mode = m;
  document.querySelectorAll('.mode-btn').forEach((b) =>
    b.classList.toggle('active', b.dataset.mode === m));
  bandleOptions.classList.toggle('hidden', m !== 'bandle');
  progressMarks.classList.toggle('hidden', m !== 'heardle');
  clearTimeout(pauseTimer);
  stopBar();
  stopBufferPoll();
  closeBandle();
  audio.pause();
  audio.removeAttribute('controls');
  round = null;
  roundReady = false;
  roundViews = null;
  gameOver = false;
  selectedGuess = null;
  guessInput.value = '';
  attempt = 0;
  revealBox.classList.add('hidden');
  stemToggles.classList.add('hidden');
  roundStatus.textContent = '';
  updatePlayIcon(false);
  renderState();
  updateHints();
}

/* ----------------------- Eventos ----------------------- */

document.querySelectorAll('.mode-btn').forEach((b) =>
  b.addEventListener('click', () => setMode(b.dataset.mode)));
loadBtn.addEventListener('click', loadPlaylist);
likedBtn.addEventListener('click', loadLikedSongs);
plInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') loadPlaylist(); });
playCta.addEventListener('click', () => {
  if (mode === 'heardle') heardlePlayPause();
  else playBandle();
});
skipBtn.addEventListener('click', skip);
submitBtn.addEventListener('click', submitGuess);
newRoundBtn.addEventListener('click', newRound);
btnMute.addEventListener('click', toggleMute);
btnLoop.addEventListener('click', toggleLoop);
el('toggle-hints').addEventListener('change', (e) => {
  showHints = e.target.checked;
  applyHintsVisibility();
});

stemToggles.addEventListener('change', (e) => {
  if (e.target.classList.contains('stem-chk') && stemGain[e.target.dataset.inst]) {
    stemGain[e.target.dataset.inst].gain.value = e.target.checked ? 1 : 0;
  }
});

function buildStemToggles(inst) {
  stemToggles.innerHTML = '<span class="muted">Mezcla manual de pistas:</span>';
  inst.forEach((x) => {
    const label = document.createElement('label');
    label.className = 'stem-chip';
    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.className = 'stem-chk';
    chk.dataset.inst = x.id;
    chk.checked = true;
    label.appendChild(chk);
    label.appendChild(document.createTextNode(' ' + x.label));
    stemToggles.appendChild(label);
  });
}

markLabel(progressMarks);
el('toggle-hints').checked = showHints;
hintMsg.textContent = 'Año y vistas de la canción. Desactivalas si preferís jugar a ciegas.';
updateMuteIcon();
renderSlots();
updateButtons();
checkStatus();