# AUREA — Estado compactado (STATE.md)

Generado: 2026-11-12 (Fases 1-4c). Actualizado 2026-11-18: REFACTOR ELECTRON (rama electron-refactor).
Uso: leer este archivo y la memoria antes de seguir trabajando. No rehacer lo ya hecho.

## Ronda 2 (2026-11-18, tarde) — voces auto, limpieza de tags, ventana frameless

Tras las capturas de Alberto con la app corriendo (generación OK, 24 segs). Tres problemas reportados y resueltos:

1. **Voces no detectadas**: causa raíz hallada — los perfiles clonados de Alberto se llaman igual que voces Kokoro (`em_chunk`, `em_origins`, `em_aldi`, `em_marfil`…), y los personajes guardaban `voice=<nombre del perfil>` con `engine='kokoro'` → voz inválida. Solución: `CastManager.sanitize_cast(book_id, valid_kokoro)` en `casting/cast_manager.py` (reconciliación idempotente: nombre == perfil con muestra existente → XTTS; voz kokoro inexistente → voz del narrador/em_alex), llamada automáticamente al inicio de `run_generation()` (engine.py) y expuesta como POST `/api/books/{id}/cast-sanitize` (server.py) con botón "✨ Reconciliar voces" en la vista Reparto. NO hace falta borrar caché: la reconciliación es en BD y el audio se regenera lo que toque.
2. **"—dijo Narrador" leído en voz alta**: (a) `clean_segment_text()` en `core/segmentation.py` como defensa final: `_synth_kokoro` y la rama XTTS sintetizan SIEMPRE texto limpio; (b) `_clean_stored_segments()` en engine.py limpia los textos ya guardados en BD al arrancar el job y borra su WAV para regenerarlos; (c) endpoint POST `/api/chapters/{id}/clean` + botón "🧹 Limpiar tags" en la actionbar del Estudio; (d) `voice_tag` del jobbar ahora muestra la voz REAL usada tras cualquier fallback (antes decía "voz em_chunk" aunque hubiera caído al fallback).
3. **Ventana más grande que la pantalla + barra del sistema**: `desktop/electron/main.js` ahora `frame: false` + tamaño desde `screen.getPrimaryDisplay().workArea` (nunca mayor, auto-maximiza si wa<1500x960), barra de título propia en `index.html`/`styles.css` (`.titlebar`, drag, botones min/max/cerrar con IPC `window:minimize|maximizeToggle|close`, icono max cambia con `window:maximizedChanged`, doble clic en la barra = maximizar).

4. **Unión automática de fragmentos**: `assemble_chapter()` en `core/engine.py` une TODOS los segmentos ya generados en el audio del capítulo sin sintetizar nada (usa la caché; valida que los WAV existan). Se lanza vía POST `/api/chapters/{id}/assemble` (JobManager, job kind "assemble") con botón "🔗 Unir" en la actionbar del Estudio (junto a 🔊 Escuchar). Casos de uso: re-pegar tras una generación interrumpida, tras una limpieza de tags que solo regeneró unos cuantos segmentos, o tras cambiar pausas dirigidas sin re-sintetizar. Advertencias integradas: "Sin segmentos..." / "Ningún segmento tiene audio todavía..." si falta material, y aviso de N segmentos sin audio tras unir; actualiza latest.wav y el audio del capítulo.

Extra: chip "muestra temporal" en Voces para perfiles cuyo sample apunta a `/tmp` (se pierde al reiniciar). Nota versiones: el motor v0.6 usa VOICES_ES importado de kokoro_adapter (no la constante parcial KOKORO_VOICES del server) para la validación de voces.

**Pendiente de la ronda**: validar en la laptop (`git pull; pkill -f aurea/server.py; bash aurea-electron.sh`), luego merge a master + bump 0.6.0.

## Refactor Electron v0.6 (2026-11-18) — RAMA electron-refactor

**Motivo**: Alberto pidió cambiar PySide6 por Electron con acabado profesional y premium.

**Decisión de arquitectura**: Electron/Chromium no puede ejecutar Kokoro/XTTS (la inferencia es Python local, regla AUREA), así que el motor Python se conserva y se le quita Qt; un servidor HTTP local hace de puente y toda la UI se migra a web (Electron). Motor validado intacto: solo se reorganizó.

**Qué se creó (rutas exactas, en la rama `electron-refactor`)**:
- `aurea/core/segmentation.py`: lógica pura movida de `generator.py` (segmentize, _split_chunk, _split_markers, gain_for + alias _gain_for, MAX_SEG/PAUSE_S/DIALOGUE_PAUSE_S). Compat total: `generator.py` re-exporta todo.
- `aurea/core/engine.py`: `run_generation()` Qt-free (puerto de ChapterGenerator.run: Kokoro singleton, XTTS lazy por clon, gain, concat con pausas, latest.wav) + `concat_segments()`. Callbacks: on_progress(pct,msg), on_failed(msg), on_done(msg).
- `aurea/core/generator.py`: ahora adaptador QThread fino que delega en engine.py; conserva señales y `_concat(segs, vmap, fallback, wav_path)` para main_window.py y scripts.
- `aurea/direction/engine.py`: `run_direction()` Qt-free (capa 1 reglas instantáneas idéntica; capa 2 Ollama qwen3.5:2b opcional, _chat/_parse/batch items). on_done(n_reglas, n_llm).
- `aurea/direction/director.py`: adaptador QThread fino (DirectionWorker conserva sus señales).
- `aurea/server.py`: puente HTTP stdlib (ThreadingHTTPServer, sin PySide6 ni dependencias nuevas) en 127.0.0.1:8765 (env AUREA_PORT). Endpoints: GET health / voices / job / books / books/{id}/chapters / books/{id}/characters / chapters/{id} / voice-profiles / audio (Range) / audio/peaks; POST books (importar), chapters/{id}/segments, /detect, /direct, /generate, /clean (v0.6-r2), books/{id}/cast-sanitize (v0.6-r2), voice-profiles; PUT characters/{id} (kokoro o xtts+profile); DELETE books/{id}, voice-profiles/{id}. JobManager: UN solo job a la vez (regla AUREA), log en memoria (200 líneas). Solo sirve WAV bajo ~/aurea/output (defensa de ruta), CORS abierto a localhost.
- `desktop/`: Electron. `package.json` (electron ^33, main electron/main.js, script start), `electron/main.js` (arranca server con AUREA_PYTHON o el .venv, espera health 30s, diálogos nativos de libro y muestra, ventana frameless + tamaño según workArea + controles ventana IPC, cierra server al salir), `electron/preload.js` (contextBridge: API + openBookDialog/openAudioDialog + min/max/close/isMaximized/onMaximizeChanged), `index.html` (titlebar propia + sidebar + jobbar + transporte con waveform), `styles.css` (Neon HUD premium: cian #00f3ff primario, magenta #ff2d78 destructivo, ámbar #ffb020, verde #2dff9e, brackets en .hud, chaflanes clip-path, chips mono uppercase, scrollbars, toasts, .titlebar drag), `app.js` (vanilla SPA: 4 vistas Biblioteca/Estudio/Reparto/Voces, polling /api/job cada 800ms con re-render de segmentos, waveform con picos del server, transporte <audio> con progreso, guardado de reparto, botón 🧹 Limpiar tags, botón ✨ Reconciliar voces en Reparto).
- `scripts/setup_electron.sh` (npm install + prueba de imports del server SIN PySide6) y `aurea-electron.sh` (arranque: npm start).

**Errata corregida**: el STATE v0.5 decía "audio_processor integrado", pero el commit v0.5 lo REMOVIÓ: `generator.py` actual no usa `audio_processor` (verificado en el código).

**Commits de la rama**: `b93335c` motor Qt-free + server, `6cf5e14` desktop Electron, `c5ab052` fixes tras 1ª captura, `68a7b60` fix AttributeError VOICES_ES, `644e95a` saneo de voces inválidas, y la ronda 2 (voces auto + clean tags + frameless) como commit único.

**Pendiente (en orden)**:
1) En la laptop: `git pull && bash aurea-electron.sh` (tras `pkill -f aurea/server.py` si quedó vivo)
2) Validar: Generar (debe sanear reparto y regenerar los segments con "—dijo Narrador."), 🧹 Limpiar tags, ✨ Reconciliar voces en Reparto, ventana sin marco (min/max/cerrar/drag) y tamaño correcto.
3) Tras validar: merge a master y bump de versión en `aurea/__init__.py` (0.6.0).
4) Heredados de v0.5 (esperando archivos de Alberto): importar guion.md de EL QUINTO SOL (6 personajes, 99 segmentos) y aplicar direccion-larga.csv como prueba de pipeline.

**Riesgo conocido**: `voice/clone_store.py` usa la BD en la RAÍZ del repo, mientras `core/database.py` usa `~/aurea/aurea.db`. Hoy coinciden porque el repo vive en `/home/aegis2/aurea`. Si se clona en otra ruta, la BD se parte en dos. Al integrar, unificar clone_store sobre Database.DB_PATH.

## Qué se está construyendo
AUREA Audiobook Studio — estudio local de audiolibros dramatizados, 100% offline tras la instalación. Stack: Python 3.12 + venv (`.venv`), UI Electron (refactor v0.6; PySide6 conserva compat), SQLite (`aurea.db`), Kokoro ONNX (TTS base), XTTS v2 (clonación de voz, preparada), Misaki/espeak-ng (G2P español).

## Qué ya quedó hecho (rutas exactas)
Fases aplicadas sobre el código en disco:
- Fase 1 Voice Lab: `.venv` creado, modelos en `models/` (`kokoro-v1.0.fp16.onnx`, `kokoro-v1.0.int8.onnx`, `voices-v1.0.bin`), 6 pruebas + 1 comparativa en `results/2026-11-10_2347/`. RTF < 1 en fp16.
- Fase 2 Núcleo: importación TXT/EPUB/PDF, SQLite (libros/capítulos/segmentos), generador por lotes en segundo plano con caché y reanudación, reproducción en app.
- Fase 3 Reparto: `casting/detector.py` (analyze divide por líneas, detecta personajes en guiones con una línea de diálogo por párrafo), `casting/cast_manager.py`, tab `characters`, columnas `kind`/`character_id` en `segments`.
- Fase 4b Director Híbrido: `direction/rules.py`, `direction/director.py` (reglas + Ollama opcional), columnas de dirección en `segments`.
- Fase 4c Clonación XTTS: `voice/clone_store.py`, `voice/sample_prep.py`, `voice/xtts_adapter.py`, `ui/clone_panel.py` (simplificado), `scripts/smoke_fase4c.py`. Reparte asigna voces XTTS automáticamente (`casting/cast_manager.py`). Requiere `pip install coqui-tts` + PyTorch/CPU.
- Reglas de pausas (v2): `direction/rules.py` con vocabularios de espec 4.1 (12 emociones, 6 volúmenes, 5 ritmos), defaults de volumen por verbo (4.2), `direct_segment` rellena pausas de TODOS los segmentos (nunca NULL), prioridad marcador > hablante > puntuación > 0. `core/generator.py` (`segmentize` procesa marcadores; `_concat` honra `pause_after_ms` y elimina pausa duplicada). `core/database.py` (`add_segment` acepta `pause_after_ms`, `update_character` con `engine`/`profile_id`, `add_character_with_profile`).
- Soporte Markdown .md (v2): `ingestion/md_reader.py` (H1=nuevo capítulo con segmento de anuncio narrador + pausa 800ms; H2/H3=corte de sección con pausa 800/500ms; `---`=800ms; limpieza de formato; listas como segmentos con pausa de coma 180ms; marcadores de pausa), `ingestion/importer.py` (registra .md), `ui/main_window.py` (.md en filtro de archivo).
- Tema Cyberpunk Neon para la GUI PySide6: `ui/theme.py` (paleta COLORS cian #00f3ff + magenta #ff00ff + texto #e8fbff + fondo #080c18, QSS global con bordes de color, `apply_dark_palette()`, `HudPanel`, `Sidebar`, `WaveformView`, `toast`). Integrado en `main_window.py`.
- Flujo dirigir-antes-de-generar: `main_window.py:_generate_chapter_with_direction()`; DirectionWorker + ChapterGenerator encadenados.
- Clonación XTTS en reparto: `casting/cast_manager.py`, `voice_map` devuelve `(engine, voz, velocidad)`, `ui/clone_panel.py`.

Archivos fuente clave (`/home/aegis2/aurea/aurea/`):
- `__init__.py` (versión `0.4b+4c`)
- `core/models.py` (Character con `engine` y `voice_profile_id`; Segment con campos de dirección)
- `core/database.py` (`add_segment` con `pause_after_ms`; `add_character_with_profile`; `update_character` con `engine`/`profile_id`; migraciones de clonación y dirección)
- `core/segmentation.py` (NUEVO v0.6: segmentize y utilidades puras, Qt-free; v0.6-r2: clean_segment_text)
- `core/engine.py` (NUEVO v0.6: run_generation Qt-free + concat_segments; v0.6-r2: sanitize_cast + limpieza de textos guardados + voice_tag real)
- `core/generator.py` (adaptador QThread fino desde v0.6)
- `ingestion/{base,text_reader,epub_reader,pdf_reader,md_reader,importer}.py`
- `direction/{rules,engine,director}.py` (engine NUEVO v0.6 Qt-free)
- `casting/{detector,cast_manager}.py` (v0.6-r2: sanitize_cast)
- `voice/{clone_store,sample_prep,xtts_adapter}.py`
- `server.py` (NUEVO v0.6: puente HTTP para Electron; v0.6-r2: /clean y /cast-sanitize)
- `ui/{main_window,theme,clone_panel,__init__}.py` (PySide6, compatible)

Scripts:
- `scripts/verificar_pausas.py` · `verificar_md.py` · `verificar_direccion.py` · `verificar_wav_capitulo.py` · `aplicar_direccion.py` · `smoke_fase4c.py`
- `scripts/setup_electron.sh` (NUEVO v0.6) + `aurea-electron.sh` (raíz)

## Valores de la tabla de pausas (espec implementada)
| Fuente | Valor | Notas |
|--------|-------|-------|
| Marcador `[pausa=Xs]` / `[silencio=Xs]` | X segundos exactos | Rango 0.1–3.0s, recortado al límite. Máximo 3000 ms solo para marcadores explícitos. |
| Cambio de hablante | 500 ms | pause_after_ms del segmento anterior. No se duplica en el generador. |
| Punto / ? / ! | 450 ms | Base de puntuación |
| Puntos suspensivos … | 700 ms | |
| Coma , | 180 ms | |
| Punto y coma / dos puntos ; : | 300 ms | |
| Raya — | 250 ms | |
| Sin puntuación | 180 ms | Se trata como coma |
| Ritmo lento | ×1.5 | Multiplicador sobre puntuación |
| Ritmo pausado | ×1.8 | |
| Ritmo rápido | ×0.7 | |
| Ritmo entrecortado | ×0.8 | |
| Emoción miedo/tristeza | ×1.3 | Multiplicador sobre puntuación |
| Emoción determinación | ×1.2 | |
| Emoción enojo/enfado | ×0.8 | |
| Emoción alegría | ×1.1 | |
| Emoción sorpresa | ×1.15 | |
| Emoción desprecio | ×0.9 | |
| Emoción ternura | ×0.95 | |
| Emoción orgullo | ×1.05 | |
| Emoción vergüenza | ×0.85 | |
| Emoción desesperación | ×1.25 | |
| Emoción neutral | ×1.0 | |
| Pregunta final de diálogo (?) | +200 ms | Sobre el valor calculado |
| Intensidad 5 | +300 ms | Golpe dramático |
| Último segmento de un capítulo | +800 ms | Cola final |
| Inicio de capítulo (pause_before_ms) | 600 ms | Solo primer segmento |
| Resto de segmentos (pause_before_ms) | 0 ms | La pausa vive en pause_after_ms del anterior |

## Vocabularios de dirección (Espec 4.1)
- Emoción (12): neutral, alegría, tristeza, enojo, miedo, sorpresa, desprecio, ternura, orgullo, vergüenza, desesperación, determinación
- Volumen/Entrega (6): susurro, bajo, normal, firme, elevado, grito
- Ritmo (5): lento, pausado, normal, rápido, entrecortado
- Intensidad: 1–5

## Defaults de volumen por verbo (Espec 4.2)
- susurró/murmuró/balbuceó/cuchicheó → susurro
- exclamó → elevado
- gritó → grito
- ordenó/advirtió/concluyó → firme
- dijo/respondió/preguntó/explicó/añadió/señaló/comentó → normal

## Decisiones tomadas
- Voz narradora por defecto: Kokoro fp16, `em_alex`.
- Un solo modelo cargado a la vez (regla AUREA): KokoroAdapter singleton + JobManager del servidor.
- Metadatos en SQLite; el audio (WAV) vive en disco bajo `output/book_<id>/`.
- Dirección híbrida: reglas instantáneas para lo literal; Ollama solo como refinamiento opcional.
- XTTS reservado para voces clonadas; Kokoro para el resto.
- Direct_segment y aplicar_direccion son idempotentes.
- pause_before_ms solo al inicio del capítulo (600ms); el resto va en pause_after_ms del segmento anterior.
- Marcadores `[` nunca llegan al TTS: dos capas de defensa (parse_markers en segmentize + clean_segment_text en el motor).
- Redondeo: truncamiento (`int()`).
- Refactor v0.6: Electron = capa de presentación; motor Python local sin Qt (server.py); PySide6 sigue funcionando como adaptador; sin dependencias nuevas de Python.
- Ronda 2: reconciliación de voces automática (sanitize_cast en cada generación, sin caché que borrar); limpieza de etiquetas de habla en 3 capas (segmentize, arranque de job, síntesis); ventana frameless con titlebar propia y tamaño según workArea.
- 4 personajes de EL QUINTO SOL (Moctezuma, Tlilpotonqui, Iztacxochitl, Xochitl) SIN voz asignada (esperando a Alberto).

## Resultado validado
- `scripts/verificar_pausas.py`: TODAS LAS PRUEBAS PASAN (20 casos).
- `scripts/verificar_md.py`: TODAS LAS PRUEBAS PASAN.
- `scripts/verificar_direccion.py`: TODAS LAS PRUEBAS PASAN.
- Detector de personajes: 7 personajes del cap. 2 de EL QUINTO SOL detectados.
- `scripts/verificar_wav_capitulo.py`: OK (duración WAV = suma segmentos + pausas, sin clipping, sin corchetes).
- `scripts/smoke_fase4c.py`: OK (RTF 4.98 | RAM pico 4432 MB).
- Refactor v0.6: probado en runtime por Alberto (capturas 2026-11-18 19:41-19:53): generación OK, 24/24 segmentos fluyendo tras el fix de VOICES_ES. Ronda 2: SIN probar en runtime aún (este entorno no ejecuta Node/Python); validar en la laptop.

## Problemas conocidos (audio / TTS)
- Pronunciación en español con Kokoro fp16: "baja" suena como "paja", nombres propios enredados. Limitación del G2P (Misaki/espeak-ng), no del código.
- Ruta doble de BD (clone_store vs database) si el repo se clona fuera de /home/aegis2 — ver riesgo en la sección Electron.
- El CSP del desktop fija media/connect a 127.0.0.1:8765; si se cambia AUREA_PORT hay que actualizar index.html.
- Perfiles de voz con muestra en /tmp (Voz Prueba, Voz de Maria en las capturas): los ficheros mueren al reiniciar; clone_store.clone_sample_map() no comprueba existencia (sanitize_cast SÍ la comprueba). Recomendado: reimportar esas muestras desde una carpeta estable o eliminarlas.

## Regla de trabajo (compacción)
En cada pausa natural, revisar el consumo de contexto. Si supera ~70 %, escribir/actualizar `STATE.md` antes de continuar: qué se construye, qué está hecho (rutas exactas), qué falta, decisiones y siguiente paso. Al iniciar o reanudar, leer primero `STATE.md`; si no existe, partir de cero sin asumir progreso previo (no es error).
