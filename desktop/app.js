/* ============================================================
   AUREA Studio — renderer (vanilla JS, sin frameworks)
   Vistas: Biblioteca · Estudio · Reparto · Voces
   Transporte con waveform; polling de jobs contra /api/job.
   v0.6: ventana frameless (barra de título propia), limpieza de
   etiquetas de habla y reconciliación automática de voces.
   ============================================================ */
'use strict';

const A = window.aurea;
const $ = (sel, root = document) => root.querySelector(sel);

const state = {
  view: 'library',
  bookId: null,
  bookTitle: '',
  chapterId: null,
  chapterData: null,
  profiles: [],
  kokoroVoices: ['em_alex', 'ef_dora', 'em_santa'],
  pollTimer: null,
  wavePeaks: null,
};

/* ---------------- utilidades ---------------- */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m + ':' + String(s).padStart(2, '0');
}

function toast(msg, kind = 'info') {
  const t = el('div', 'toast ' + kind, String(msg));
  $('#toasts').appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 300);
  }, 4500);
}

/* ---------------- ventana (barra de título propia) ---------------- */
$('#tb-min').addEventListener('click', () => A.minimizeWindow());
$('#tb-max').addEventListener('click', () => A.toggleMaximize());
$('#tb-close').addEventListener('click', () => A.closeWindow());
$('#titlebar').addEventListener('dblclick', (e) => {
  if (e.target.closest('.tb-btn')) return;
  A.toggleMaximize();
});
if (A.onMaximizeChanged) {
  A.onMaximizeChanged((max) => {
    const b = $('#tb-max');
    b.innerHTML = max ? '&#x274F;' : '&#x25A1;';
    b.title = max ? 'Restaurar' : 'Maximizar';
  });
}

/* ---------------- api ---------------- */
async function api(method, path, body) {
  const opt = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const res = await fetch(A.API + path, opt);
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(data.error || 'HTTP ' + res.status);
  return data;
}

const get = (p) => api('GET', p);
const post = (p, b) => api('POST', p, b ?? {});
const put = (p, b) => api('PUT', p, b ?? {});
const del = (p) => api('DELETE', p);

/* ---------------- health ---------------- */
async function healthCheck() {
  const dot = $('#conn-dot');
  const label = $('#conn-label');
  try {
    await get('/api/health');
    dot.className = 'dot dot-ok';
    label.textContent = 'motor listo';
    return true;
  } catch (_) {
    dot.className = 'dot dot-ko';
    label.textContent = 'sin conexión';
    return false;
  }
}

/* ---------------- jobs / barra ---------------- */
const KIND_LABEL = {
  generate: 'GENERANDO',
  direction: 'DIRIGIENDO',
  'direction+ollama': 'DIRIGIENDO + OLLAMA',
  assemble: 'UNIENDO',
  scratch: 'LABORATORIO',
  'voice-test': 'PRUEBA DE VOZ',
};

function renderJob(job) {
  const bar = $('#jobbar');
  bar.hidden = job.state === 'idle';
  bar.className = 'jobbar ' + job.state;
  $('#job-kind').textContent = KIND_LABEL[job.kind] || (job.kind || '').toUpperCase();
  const pct = job.state === 'running' ? (job.progress || 0) : 100;
  $('#job-fill').style.width = pct + '%';
  $('#job-msg').textContent = job.message || '';
  document.body.classList.toggle('job-running', job.state === 'running');
}

async function startPolling() {
  $('#jobbar').hidden = false;
  if (state.pollTimer) return;
  state.pollTimer = setInterval(async () => {
    let job;
    try { job = await get('/api/job'); } catch (_) { return; }
    renderJob(job);
    if (job.state === 'running') {
      if (state.view === 'studio' && state.chapterId) {
        try { state.chapterData = await get('/api/chapters/' + state.chapterId); }
        catch (_) {}
        renderChapterDetail(true);
      }
    } else {
      stopPolling();
      if (job.state === 'done') {
        toast('Listo: ' + (job.message || ''), 'ok');
        if (state.autoplayResult && job.result && job.result.audio_url) {
          state.autoplayResult = false;
          playAudio(job.result.audio_url,
            job.result.character
              ? job.result.character + ' (prueba de voz)'
              : 'Laboratorio');
        }
      } else if (state.autoplayResult) {
        state.autoplayResult = false;
      }
      if (job.state === 'failed') toast('Fallo: ' + (job.message || ''), 'err');
      rerender();
    }
  }, 800);
}

function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}

function rerender() {
  ({ library: viewLibrary, studio: viewStudio, cast: viewCast, voices: viewVoices, lab: viewLab }[state.view] || viewLibrary)();
}

/* ---------------- navegación ---------------- */
function setActiveNav(view) {
  document.querySelectorAll('.nav-item').forEach(
    (b) => b.classList.toggle('active', b.dataset.view === view));
  state.view = view;
}

document.querySelectorAll('.nav-item').forEach((b) => {
  b.addEventListener('click', () => rerenderTo(b.dataset.view));
});

function rerenderTo(view) {
  ({ library: viewLibrary, studio: viewStudio, cast: viewCast, voices: viewVoices, lab: viewLab }[view])();
}

/* ---------------- biblioteca ---------------- */
async function viewLibrary() {
  setActiveNav('library');
  const v = $('#view');
  const books = await get('/api/books');
  v.innerHTML = '';
  const head = el('div', 'view-head');
  head.appendChild(el('h1', null, 'Biblioteca'));
  const btnImport = el('button', 'btn btn-primary', '＋ Importar libro');
  btnImport.onclick = importBook;
  head.appendChild(btnImport);
  v.appendChild(head);

  const grid = el('div', 'book-grid');
  if (!books.length) {
    grid.appendChild(el('div', 'empty',
      'Aún no hay libros. Importa un TXT, Markdown, EPUB o PDF para empezar.'));
  }
  for (const b of books) {
    const card = el('div', 'book-card hud');
    card.appendChild(el('div', 'book-fmt mono', (b.fmt || '').toUpperCase()));
    card.appendChild(el('div', 'book-title', b.title));
    const row = el('div', 'book-actions');
    const open = el('button', 'btn btn-ghost', 'Abrir');
    open.onclick = () => { state.bookId = b.id; state.bookTitle = b.title; viewStudio(); };
    const delBtn = el('button', 'btn btn-ghost danger', 'Eliminar');
    delBtn.onclick = async () => {
      if (!confirm('¿Eliminar "' + b.title + '" y su audio generado?')) return;
      try { await del('/api/books/' + b.id); toast('Libro eliminado', 'ok'); }
      catch (e) { toast(e.message, 'err'); }
      viewLibrary();
    };
    row.append(open, delBtn);
    card.appendChild(row);
    grid.appendChild(card);
  }
  v.appendChild(grid);
}

async function importBook() {
  const path = await A.openBookDialog();
  if (!path) return;
  toast('Importando…');
  try {
    const r = await post('/api/books', { path });
    toast(`"${r.title}" importado (${r.chapters} capítulos)`, 'ok');
    viewLibrary();
  } catch (e) { toast('Error al importar: ' + e.message, 'err'); }
}

/* ---------------- estudio ---------------- */
async function viewStudio() {
  setActiveNav('studio');
  const v = $('#view');
  v.innerHTML = '';
  if (!state.bookId) {
    v.appendChild(el('div', 'empty',
      'Elige un libro en la Biblioteca para entrar al Estudio.'));
    return;
  }
  const chapters = await get(`/api/books/${state.bookId}/chapters`);
  state.chapters = chapters;
  if (!state.chapterId && chapters.length) state.chapterId = chapters[0].id;

  const wrap = el('div', 'studio');
  const left = el('div');
  const listHead = el('div', 'view-head');
  const h1 = el('h1', 'ellipsis', state.bookTitle);
  h1.title = state.bookTitle;
  h1.style.cursor = 'pointer';
  h1.onclick = viewLibrary;
  listHead.appendChild(h1);
  left.appendChild(listHead);
  listHead.style.marginBottom = '12px';
  const list = el('div', 'ch-list');
  if (!chapters.length) list.appendChild(el('div', 'empty', 'Este libro no tiene capítulos.'));
  for (const c of chapters) {
    const item = el('div', 'ch-item' + (c.id === state.chapterId ? ' sel' : ''));
    const ct = el('div', 'ch-title ellipsis', `${c.idx + 1}. ${c.title}`);
    ct.title = c.title;
    item.appendChild(ct);
    const meta = el('div', 'ch-meta');
    const pct = c.segments ? Math.round(100 * c.done / c.segments) : 0;
    const bar = el('div', 'bar mini-bar');
    const fill = el('div', 'bar-fill');
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    meta.append(bar, el('span', null, `${c.done}/${c.segments}`));
    item.appendChild(meta);
    item.onclick = () => loadChapter(c.id);
    list.appendChild(item);
  }
  left.appendChild(list);
  const right = el('div');
  right.id = 'chapter-detail';
  wrap.append(left, right);
  v.appendChild(wrap);
  if (state.chapterId) await loadChapter(state.chapterId);
}

async function loadChapter(id) {
  state.chapterId = id;
  document.querySelectorAll('.ch-item').forEach(
    (elm, i) => elm.classList.toggle('sel', state.chapters[i] && state.chapters[i].id === id));
  try { state.chapterData = await get('/api/chapters/' + id); }
  catch (e) { toast(e.message, 'err'); return; }
  renderChapterDetail();
}

function renderChapterDetail(polling = false) {
  const d = state.chapterData;
  const right = $('#chapter-detail');
  if (!d || !right) return;
  const scroller = $('#view');
  const savedScroll = scroller.scrollTop;
  right.innerHTML = '';
  const ch = d.chapter;

  const head = el('div', 'chapter-head');
  const ht = el('h2', 'ellipsis', `${ch.idx + 1}. ${ch.title}`);
  ht.title = ch.title;
  head.appendChild(ht);
  head.appendChild(el('span', 'mono',
    `${d.done}/${d.total} segmentos con audio`));
  right.appendChild(head);

  const bar = el('div', 'actionbar');
  const btnDetect = el('button', 'btn', '⛳ Detectar reparto');
  btnDetect.onclick = () => doAction('Detectando reparto…', `/api/chapters/${ch.id}/detect`);
  const btnEnsure = el('button', 'btn btn-ghost', '▣ Crear segmentos');
  btnEnsure.onclick = () => doAction('Creando segmentos…', `/api/chapters/${ch.id}/segments`);
  const btnClean = el('button', 'btn btn-ghost', '🧹 Limpiar tags');
  btnClean.title = 'Quita etiquetas de habla ("—dijo Narrador.") de los segmentos; el audio afectado se regenera';
  btnClean.onclick = async () => {
    try {
      const r = await post(`/api/chapters/${ch.id}/clean`);
      if (r.cleaned) {
        toast(`Limpiados ${r.cleaned} segmentos; regenera para actualizar el audio`, 'ok');
        state.chapterData = await get('/api/chapters/' + ch.id);
        renderChapterDetail();
      } else {
        toast('Sin etiquetas de habla: nada que limpiar', 'ok');
      }
    } catch (e) { toast(e.message, 'err'); }
  };
  const lbl = el('label', 'check');
  const llm = el('input');
  llm.type = 'checkbox';
  lbl.append(llm, document.createTextNode('Ollama'));
  const btnDirect = el('button', 'btn btn-primary', '🎬 Dirigir');
  btnDirect.onclick = async () => {
    try {
      await post(`/api/chapters/${ch.id}/segments`);
      await post(`/api/chapters/${ch.id}/direct`, { use_llm: llm.checked });
      toast('Dirección en marcha…');
      startPolling();
    } catch (e) { toast(e.message, 'err'); }
  };
  const btnGen = el('button', 'btn btn-primary', '▶ Generar');
  btnGen.onclick = async () => {
    try {
      await post(`/api/chapters/${ch.id}/segments`);
      await post(`/api/chapters/${ch.id}/generate`, { force: false });
      toast('Generación en marcha…');
      startPolling();
    } catch (e) { toast(e.message, 'err'); }
  };
  const btnJoin = el('button', 'btn btn-ghost', '🔗 Unir');
  btnJoin.title = 'Une todos los fragmentos ya generados en el audio del capítulo, sin regenerar nada';
  btnJoin.onclick = async () => {
    try {
      await post(`/api/chapters/${ch.id}/assemble`);
      toast('Unión en marcha…');
      startPolling();
    } catch (e) { toast(e.message, 'err'); }
  };
  const btnListen = el('button', 'btn', '🔊 Escuchar');
  btnListen.onclick = () => {
    if (!d.latest_audio) { toast('Aún no hay audio del capítulo'); return; }
    playAudio(d.latest_audio, `Cap ${ch.idx + 1} · ${ch.title}`);
  };
  const btnForce = el('button', 'btn btn-magenta', '↻ Regenerar todo');
  btnForce.onclick = async () => {
    if (!confirm('Regenerar todos los segmentos del capítulo (borra el audio cacheado)?')) return;
    await post(`/api/chapters/${ch.id}/segments`);
    await post(`/api/chapters/${ch.id}/generate`, { force: true });
    toast('Regenerando…');
    startPolling();
  };
  bar.append(btnDetect, btnEnsure, btnClean, lbl, btnDirect,
             el('span', 'spacer'), btnJoin, btnListen, btnGen, btnForce);
  right.appendChild(bar);

  const list = el('div', 'seg-list');
  if (!d.segments.length) {
    list.appendChild(el('div', 'empty',
      'Sin segmentos todavía. Pulsar ▶ Generar los crea automáticamente.'));
  }
  for (const s of d.segments) list.appendChild(segRow(s));
  right.appendChild(list);
  scroller.scrollTop = savedScroll;
}

function segRow(s) {
  const row = el('div', 'seg ' + (s.kind === 'dialogue' ? 'dialogue' : 'narration'));
  row.appendChild(el('div', 'seg-idx', String(s.idx + 1).padStart(2, '0')));
  const body = el('div', 'seg-body');
  body.appendChild(el('div', 'seg-text', s.text));
  const chips = el('div', 'seg-chips');
  if (s.character_name) {
    chips.appendChild(el('span',
      'chip ' + (s.character_name === 'Narrador' ? 'chip-amber' : 'chip-cyan'),
      s.character_name));
  }
  if (s.directed) {
    chips.appendChild(el('span', 'chip', s.emotion || 'neutral'));
    chips.appendChild(el('span', 'chip', s.delivery || 'normal'));
    chips.appendChild(el('span', 'chip chip-amber', '×' + (s.pace ?? 1).toFixed(2)));
    chips.appendChild(el('span', 'chip chip-amber', '⏸ ' + (s.pause_after_ms ?? 0) + 'ms'));
  } else {
    chips.appendChild(el('span', 'chip chip-magenta', 'sin dirigir'));
  }
  if (s.has_audio) chips.appendChild(el('span', 'chip chip-green', 'con audio'));
  body.appendChild(chips);
  row.appendChild(body);
  if (s.audio_url) {
    const play = el('button', 'btn btn-ghost btn-icon', '♪');
    play.title = 'Escuchar segmento';
    play.onclick = () => playAudio(s.audio_url, `Seg ${s.idx + 1}`);
    row.appendChild(play);
  } else {
    row.appendChild(el('div'));
  }
  return row;
}

async function doAction(msg, path) {
  toast(msg);
  try {
    const r = await post(path);
    if (r.detected) {
      toast(`Detectados: ${r.detected.join(', ') || 'ninguno'} · ${r.created} nuevos`, 'ok');
    } else if (r.created !== undefined) {
      toast(`Segmentos listos (${r.created} creados)`, 'ok');
    } else {
      toast('Listo', 'ok');
    }
  } catch (e) { toast(e.message, 'err'); }
  rerender();
}

/* ---------------- reparto ---------------- */
async function viewCast() {
  setActiveNav('cast');
  const v = $('#view');
  v.innerHTML = '';
  if (!state.bookId) {
    v.appendChild(el('div', 'empty', 'Elige un libro en la Biblioteca para ver su reparto.'));
    return;
  }
  const head = el('div', 'view-head');
  head.appendChild(el('h1', null, 'Reparto — ' + state.bookTitle));
  const btnSanitize = el('button', 'btn', '✨ Reconciliar voces');
  btnSanitize.title = 'Detecta perfiles de voz clonada guardados y se los asigna a los personajes; corrige voces Kokoro inexistentes';
  btnSanitize.onclick = async () => {
    try {
      const r = await post(`/api/books/${state.bookId}/cast-sanitize`);
      toast(r.fixed ? `${r.fixed} personaje(s) reconciliados automáticamente` :
             'Todas las voces ya estaban bien', 'ok');
      setTimeout(viewCast, 300);
    } catch (e) { toast(e.message, 'err'); }
  };
  const btnDetect = el('button', 'btn btn-primary', '⛳ Detectar reparto');
  head.append(btnSanitize, btnDetect);
  v.appendChild(head);

  let chars = [];
  if (state.chapterData && state.chapterData.chapter &&
      state.chapterData.chapter.book_id === state.bookId &&
      state.chapterData.characters) {
    chars = state.chapterData.characters;
  } else {
    try { chars = await get(`/api/books/${state.bookId}/characters`); }
    catch (_) { chars = []; }
  }
  const profiles = await get('/api/voice-profiles');
  state.profiles = profiles;

  const table = el('div', 'table');
  if (!chars.length) {
    table.appendChild(el('div', 'empty',
      'Sin reparto. Abre un capítulo en Estudio y pulsa "Detectar reparto".'));
    v.appendChild(table);
    fbDetect(btnDetect);
    return;
  }
  const names = { 'em_alex': 'Alex (m)', 'ef_dora': 'Dora (f)', 'em_santa': 'Santa (f)' };
  for (const c of chars) {
    const row = el('div', 'row hud');
    const name = el('div', 'name', c.name);
    if (c.is_narrator) name.appendChild(el('span', 'chip chip-amber', 'Narrador'));
    if (c.engine === 'xtts') name.appendChild(el('span', 'chip chip-magenta', 'Clon'));
    if (c.engine !== 'xtts' && !names[c.voice]) {
      name.appendChild(el('span', 'chip chip-magenta',
        `voz inválida: ${c.voice}`));
    }
    row.appendChild(name);

    const engSel = el('select');
    engSel.append(
      new Option('Kokoro (voz base)', 'kokoro', false, c.engine !== 'xtts'),
      new Option('XTTS (voz clonada)', 'xtts', false, c.engine === 'xtts'));
    row.appendChild(engSel);

    const voiceSel = el('select');
    for (const scan of state.kokoroVoices) {
      voiceSel.append(new Option(names[scan] || scan, scan, false, c.voice === scan));
    }
    row.appendChild(voiceSel);

    const profSel = el('select');
    profSel.appendChild(new Option('— elegir perfil —', '', false, false));
    for (const p of profiles) {
      profSel.append(new Option(p.name, String(p.id), false,
        c.engine === 'xtts' && c.voice_profile_id === p.id));
    }
    row.appendChild(profSel);

    const speed = el('input');
    speed.type = 'number';
    speed.min = '0.5'; speed.max = '2'; speed.step = '0.05';
    speed.value = c.speed ?? 1;
    row.appendChild(speed);

    const test = el('button', 'btn btn-ghost', 'Probar');
    test.title = 'Escucha una frase generada con esta voz, tal como sonará en la lectura';
    test.onclick = () => testVoice(c);
    const save = el('button', 'btn', 'Guardar');
    save.onclick = () => doSave(c, engSel, voiceSel, profSel, speed);
    row.appendChild(save);
    row.appendChild(test);
    table.appendChild(row);
  }
  v.appendChild(table);
  fbDetect(btnDetect);
}

function fbDetect(btn) {
  btn.onclick = async () => {
    if (!state.chapterId) { toast('Abre primero un capítulo en Estudio', 'err'); return; }
    try {
      const r = await post(`/api/chapters/${state.chapterId}/detect`);
      toast(`Detectados: ${r.detected.join(', ') || 'ninguno'} (${r.created} nuevos)`, 'ok');
    } catch (e) { toast(e.message, 'err'); }
    setTimeout(viewCast, 400);
  };
}

async function doSave(c, engSel, voiceSel, profSel, speed) {
  try {
    if (engSel.value === 'xtts') {
      await put(`/api/characters/${c.id}`,
        { engine: 'xtts', profile_id: Number(profSel.value), speed: Number(speed.value) });
      toast(`${c.name} → voz clonada XTTS`, 'ok');
    } else {
      await put(`/api/characters/${c.id}`,
        { engine: 'kokoro', voice: voiceSel.value, speed: Number(speed.value) });
      toast(`${c.name} → ${voiceSel.value} (Kokoro)`, 'ok');
    }
  } catch (e) { toast(e.message, 'err'); }
  viewCast();
}

async function testVoice(c) {
  try {
    await post(`/api/characters/${c.id}/voice-test`);
    state.autoplayResult = true;
    toast(`Generando prueba de ${c.name}…`);
    startPolling();
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------------- voces clonadas ---------------- */
async function viewVoices() {
  setActiveNav('voices');
  const v = $('#view');
  v.innerHTML = '';
  const head = el('div', 'view-head');
  head.appendChild(el('h1', null, 'Voces clonadas (XTTS)'));
  v.appendChild(head);

  const form = el('div', 'form-row hud');
  form.style.padding = '14px 16px';
  const name = el('input');
  name.type = 'text';
  name.placeholder = 'Nombre del perfil (ej. Yo — narrador)';
  const pick = el('button', 'btn btn-ghost', 'Elegir muestra…');
  pick.onclick = async () => {
    const path = await A.openAudioDialog();
    if (!path) return;
    if (!name.value.trim()) { toast('Escribe primero un nombre de perfil', 'err'); return; }
    toast('Analizando muestra…');
    try {
      const r = await post('/api/voice-profiles', { name: name.value.trim(), path });
      const rep = r.report;
      toast(`Muestra ${rep.verdict}: ${rep.duration}s, SNR ${rep.snr_db}dB`, 'ok');
      viewVoices();
    } catch (e) { toast('Muestra rechazada: ' + e.message, 'err'); }
  };
  form.append(name, pick);
  v.appendChild(form);

  const profiles = await get('/api/voice-profiles');
  state.profiles = profiles;
  const grid = el('div', 'profile-grid');
  if (!profiles.length) {
    grid.appendChild(el('div', 'empty',
      'Sin perfiles. Cada perfil necesita una muestra limpia de 10-30s (MP3/WAV/FLAC).'));
  }
  for (const p of profiles) {
    const card = el('div', 'profile-card hud');
    const head = el('div', 'profile-head');
    head.appendChild(el('div', 'pname', p.name));
    const play = el('button', 'btn btn-icon', '▶');
    play.title = 'Escuchar la muestra de esta voz';
    play.onclick = () => playAudio(`/api/voice-profiles/${p.id}/audio`, `${p.name} (muestra)`);
    head.appendChild(play);
    card.appendChild(head);
    const chips = el('div', 'profile-chips');
    chips.appendChild(el('span', 'chip chip-cyan',
      String(p.sample_path || '').split('/').pop()));
    // Aviso si la muestra vive en /tmp (se pierde al reiniciar)
    const sp = String(p.sample_path || '');
    if (sp.startsWith('/tmp/')) {
      chips.appendChild(el('span', 'chip chip-amber', 'muestra temporal'));
    }
    card.appendChild(chips);
    const uses = p.used_by || [];
    for (const u of uses) {
      chips.appendChild(el('span', 'chip chip-green',
        u.character + (u.is_narrator ? " ★" : "") + " · " + u.book));
    }
    if (!uses.length) {
      chips.appendChild(el('span', 'chip', 'sin asignar'));
    }
    const pathLine = el('div', 'mono', sp);
    pathLine.style.cssText =
      'font-size:10px;color:var(--muted);margin-bottom:12px;word-break:break-all;';
    card.appendChild(pathLine);
    const delBtn = el('button', 'btn btn-magenta', 'Eliminar');
    delBtn.onclick = async () => {
      if (!confirm('¿Eliminar el perfil "' + p.name + '"?')) return;
      try { await del('/api/voice-profiles/' + p.id); toast('Perfil eliminado', 'ok'); }
      catch (e) { toast(e.message, 'err'); }
      viewVoices();
    };
    card.appendChild(delBtn);
    grid.appendChild(card);
  }
  v.appendChild(grid);
}

/* ---------------- laboratorio (texto libre) ---------------- */
async function viewLab() {
  setActiveNav('lab');
  const v = $('#view');
  v.innerHTML = '';
  const head = el('div', 'view-head');
  head.appendChild(el('h1', null, 'Laboratorio — texto libre'));
  v.appendChild(head);

  const wrap = el('div', 'hud lab');
  wrap.style.maxWidth = '880px';

  const ta = el('textarea', 'lab-text');
  ta.placeholder = 'Escribe aquí el texto que quieres escuchar (acepta [pausa=2s] y [silencio=1.5s])…';
  ta.value = localStorage.getItem('aurea.labText') || '';
  ta.oninput = () => localStorage.setItem('aurea.labText', ta.value);

  const row = el('div', 'form-row');
  const voiceSel = el('select');
  const ogK = el('optgroup');
  ogK.label = 'Kokoro (voces base)';
  const KN = { em_alex: 'Alex (m) — narrador por defecto', ef_dora: 'Dora (f)', em_santa: 'Santa (f)' };
  for (const scan of state.kokoroVoices) {
    ogK.appendChild(new Option(KN[scan] || scan, 'kokoro:' + scan));
  }
  voiceSel.appendChild(ogK);

  const profiles = await get('/api/voice-profiles');
  if (profiles.length) {
    const ogP = el('optgroup');
    ogP.label = 'Clones XTTS';
    for (const p of profiles) ogP.appendChild(new Option(p.name, 'xtts:' + p.id));
    voiceSel.appendChild(ogP);
  }

  if (state.bookId) {
    const chars = await get(`/api/books/${state.bookId}/characters`);
    if (chars.length) {
      const ogC = el('optgroup');
      ogC.label = 'Personajes de ' + state.bookTitle + ' (así sonarán en la lectura)';
      for (const c of chars) {
        ogC.appendChild(new Option(c.name + (c.is_narrator ? ' ★' : ''), 'char:' + c.id));
      }
      voiceSel.appendChild(ogC);
    }
  }

  const btnGo = el('button', 'btn btn-primary', '▶ Escuchar texto');
  btnGo.onclick = async () => {
    const text = ta.value.trim();
    if (!text) { toast('Escribe algo de texto primero', 'err'); return; }
    try {
      await post('/api/scratch', { text, source: voiceSel.value || 'kokoro:em_alex' });
      state.autoplayResult = true;
      toast('Sintetizando…');
      startPolling();
    } catch (e) { toast(e.message, 'err'); }
  };
  const hint = el('div', 'lab-hint', state.bookId
    ? 'Elige una voz base, un clon o un personaje del libro actual.'
    : 'Elige un libro en Biblioteca para probar también las voces del reparto.');

  row.append(voiceSel, btnGo);
  wrap.append(ta, row, hint);
  v.appendChild(wrap);
}

/* ---------------- transporte ---------------- */
const player = $('#player');
const waveCanvas = $('#wave');

function playAudio(url, title) {
  player.src = A.API + url;
  player.play().then(() => {
    $('#t-title').textContent = title;
    setPlayIcon();
    loadWave(url);
  }).catch((e) => toast('No se pudo reproducir: ' + e.message, 'err'));
}

$('#btn-play').onclick = () => {
  if (!player.src) return;
  if (player.paused) { player.play().catch(() => {}); } else { player.pause(); }
  setPlayIcon();
};

function setPlayIcon() {
  $('#btn-play').textContent = player.paused ? '▶' : '❚❚';
}

player.addEventListener('play', setPlayIcon);
player.addEventListener('pause', setPlayIcon);
player.addEventListener('ended', setPlayIcon);

async function loadWave(url) {
  try {
    const p = new URL(A.API + url).searchParams.get('path') || '';
    const res = await fetch(A.API + '/api/audio/peaks?path=' +
      encodeURIComponent(p) + '&n=240');
    const data = await res.json();
    state.wavePeaks = data.peaks || [];
    drawWave();
  } catch (_) { state.wavePeaks = null; drawWave(); }
}

function drawWave() {
  const canvas = waveCanvas;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const peaks = state.wavePeaks;
  if (!peaks || !peaks.length) {
    // Línea base tenue mientras no hay audio cargado
    ctx.strokeStyle = 'rgba(126, 147, 180, 0.25)';
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    return;
  }
  const n = peaks.length;
  const bw = Math.max(2, w / n - 1);
  ctx.fillStyle = 'rgba(0, 243, 255, 0.55)';
  for (let i = 0; i < n; i++) {
    const bH = Math.max(2, peaks[i] * h * 0.9);
    ctx.fillRect(i * (bw + 1), (h - bH) / 2, bw, bH);
  }
}

window.addEventListener('resize', drawWave);
drawWave();

player.addEventListener('timeupdate', () => {
  const dur = player.duration || 1;
  $('#wave-progress').style.width = (100 * player.currentTime / dur) + '%';
  $('#t-time').textContent =
    fmtTime(player.currentTime) + ' / ' + fmtTime(player.duration || 0);
});

/* seek con clic en la waveform */
document.querySelector('.wave-wrap').addEventListener('click', (ev) => {
  if (!player.duration) return;
  const rect = ev.currentTarget.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
  player.currentTime = frac * player.duration;
});

/* ---------------- arranque ---------------- */
healthCheck().then(() => viewLibrary());
setInterval(healthCheck, 5000);
