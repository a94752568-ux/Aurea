# AUREA — Estado compactado (STATE.md)

Generado: 2026-11-12. Actualizado tras implementar mejoras para EL QUINTO SOL (reglas de pausas v2, soporte Markdown v2, sistema de dirección, clonación XTTS integrada).
**Actualizado 2026-11-21: reparación de auditoría (Fases 1–5) + acabado cyberpunk premium + versión 0.5.0. Actualizado 2026-11-21: OCR local para PDF escaneados + versión 0.6.0. Actualizado 2026-11-21: reparto inteligente por género + ambientes con ducking + versión 0.7.0.** Ver las secciones "Reparación 2026-11-20/21", "OCR para PDF escaneados (v0.6.0)" y "Reparto inteligente por género + ambientes con ducking (v0.7.0)" al final.
Uso: leer este archivo y la memoria antes de seguir trabajando. No rehacer lo ya hecho.

## Qué se está construyendo
AUREA Audiobook Studio — estudio local de audiolibros dramatizados, 100% offline tras la instalación. Stack: Python 3.12 + venv (`.venv`), PySide6 UI, SQLite (`aurea.db`), Kokoro ONNX (TTS base), XTTS v2 (clonación de voz, preparada), Misaki/espeak-ng (G2P español).

## Qué ya quedó hecho (rutas exactas)
Fases aplicadas sobre el código en disco:
- Fase 1 Voice Lab: `.venv` creado, modelos en `models/` (`kokoro-v1.0.fp16.onnx`, `kokoro-v1.0.int8.onnx`, `voices-v1.0.bin`), 6 pruebas + 1 comparativa en `results/2026-11-10_2347/`. RTF < 1 en fp16.
- Fase 2 Núcleo: importación TXT/EPUB/PDF, SQLite (libros/capítulos/segmentos), generador por lotes en segundo plano con caché y reanudación, reproducción en app.
- Fase 3 Reparto: `casting/detector.py` (analyze divide por líneas, detecta personajes en guiones con una línea de diálogo por párrafo), `casting/cast_manager.py`, tab `characters`, columnas `kind`/`character_id` en `segments`.
- Fase 4b Director Híbrido: `direction/rules.py`, `direction/director.py` (reglas + Ollama opcional), columnas de dirección en `segments`.
- Fase 4c Clonación XTTS: `voice/clone_store.py`, `voice/sample_prep.py`, `voice/xtts_adapter.py`, `ui/clone_panel.py` (simplificado), `scripts/smoke_fase4c.py`. Reparte asigna voces XTTS automáticamente (`casting/cast_manager.py`). Requiere `pip install coqui-tts` + PyTorch/CPU.
- Reglas de pausas (v2): `direction/rules.py` con vocabularios de espec 4.1 (12 emociones, 6 volúmenes, 5 ritmos), defaults de volumen por verbo (4.2), `direct_segment` rellena pausas de TODOS los segmentos (nunca NULL), prioridad marcador > hablante > puntuación > 0. `core/generator.py` (`segmentize` procesa marcadores; `_concat` honra `pause_after_ms` y elimina pausa duplicada). `core/database.py` (`add_segment` acepta `pause_after_ms`, `update_character` con `engine`/`profile_id`, `add_character_with_profile`).
- Soporte Markdown .md (v2): `ingestion/md_reader.py` (H1=nuevo capítulo con segmento de anuncio narrador + pausa 800ms; H2/H3=corte de sección con pausa 800/500ms; `---`=800ms; limpieza de formato; listas como segmentos con pausa de coma 180ms; marcadores de pausa), `ingestion/importer.py` (registra .md), `ui/main_window.py` (.md en filtro de archivo).
- Procesamiento de audio automático: `core/audio_processor.py` (normalización RMS, compresión, limitador, potenciador de graves, herramientas estéreo). **CORREGIDO 2026-11-20 (A9):** `process_master` SÍ está integrado en `core/generator.py:_concat` (línea ~310), dentro de try/except (un fallo de procesado nunca mata el capítulo; registra aviso). Verificado de verdad: RMS crudo 0.354/0.224 → RMS final 0.127 (target 0.14). `process_segment` existe en AudioProcessor pero NO está conectado (los segmentos van en crudo a `_concat`); no afirmar lo contrario.
- Tema Cyberpunk Neon para la GUI: `ui/theme.py` (paleta COLORS cian #00f3ff + magenta #ff00ff + texto #e8fbff + fondo #080c18, QSS global con bordes de color, `apply_dark_palette()` para QPalette Fusion oscura, `HudPanel` con esquinas bracket, `Sidebar`, `WaveformView`, `toast`). Integrado en `main_window.py` vía `apply_theme` + `apply_dark_palette`.
- Flujo dirigir-antes-de-generar: `main_window.py:_generate_chapter_with_direction()` dirige automáticamente segmentos no dirigidos antes de generar audio. Botón "🎬 Dirigir" en página Estudio. `_ensure_segments()` crea segmentos automáticamente al importar un capítulo vacío. `DirectionWorker` + `ChapterGenerator` encadenados. Feedback con QMessageBox en todos los puntos de fallo. Primer capítulo seleccionado automáticamente al importar.
- Clonación XTTS en reparto: `casting/cast_manager.py` (`build_cast` asigna voces Kokoro por defecto `ef_dora`/`em_santa`; las voces clonadas XTTS se asignan desde la GUI). `voice_map` devuelve `(engine, voz, velocidad)`. `ui/clone_panel.py` (flujo simplificado: importar → asignar, con selector Kokoro/XTTS). `ui/main_window.py` (página Reparto muestra voces asignadas).

Archivos fuente clave (`/home/aegis2/aurea/aurea/`):
- `__init__.py` (versión `0.7.0` — única constante, leída por la ventana; antes `0.4b+4c` en `__init__.py` y `0.5` fijo en el título)
- `core/models.py` (Character con `engine` y `voice_profile_id`; Segment con campos de dirección)
- `core/database.py` (`add_segment` con `pause_after_ms`; `add_character_with_profile`; `update_character` con `engine`/`profile_id`; migraciones de columnas de clonación y dirección)
- `core/generator.py` (ChapterGenerator QThread; `_concat` con `pause_after_ms`, defensa contra `[` en TTS, manejo estéreo/mono; procesamiento de audio por segmento y master final vía `audio_processor`)
- `core/audio_processor.py` (normalización RMS, compresión, limitador, bass boost, herramientas estéreo)
- `ingestion/{base,text_reader,epub_reader,pdf_reader,md_reader,importer}.py`
- `direction/{rules,director}.py` (reglas de pausa con vocabularios espec 4.1; Director Híbrido con Ollama opcional)
- `casting/{detector,cast_manager}.py`
- `voice/{clone_store,sample_prep,xtts_adapter}.py`
- `ui/{main_window,theme,clone_panel,__init__}.py`

Scripts:
- `scripts/verificar_pausas.py` (pruebas de reglas de pausa)
- `scripts/verificar_md.py` (pruebas de importación Markdown)
- `scripts/verificar_direccion.py` (pruebas de aplicación de dirección)
- `scripts/verificar_wav_capitulo.py` (prueba de generación de WAV capítulo)
- `scripts/aplicar_direccion.py` (aplica CSV de dirección a segmentos)
- `scripts/smoke_fase4c.py` (prueba de clonación XTTS)

BD (`/home/aegis2/aurea/aurea.db`): libros/capítulos/segmentos/personajes/voice_profiles. Columnas `pause_before_ms`/`pause_after_ms`/`emotion`/`delivery`/`intensity`/`pace`/`directed` ya existen. Columnas `engine`/`voice_profiles.id` en `characters` ya existen.

## Valores de la tabla de pausas (espec implementada)
| Fuente | Valor | Notas |
|--------|-------|-------|
| Marcador `[pausa=Xs]` / `[silencio=Xs]` | X segundos exactos | Rango 0.1–3.0s, recortado al límite. Máximo 3000 ms solo para marcadores explícitos. |
| Cambio de hablante | 500 ms | pause_after_ms del segmento anterior. No se duplica en el generador. |
| Punto / ? / ! | 450 ms | Base de puntuación |
| Puntos suspensivos … | 700 ms | Base; con pace 0.9 el valor dirigido es 777 ms (700 × 1/0.9) |
| Coma , | 180 ms | |
| Punto y coma / dos puntos ; : | 300 ms | |
| Raya — | 250 ms | |
| Sin puntuación | 180 ms | Se trata como coma |
| Ritmo lento | pace 0.75 (velocidad) | Las pausas se alargan: base × (1/0.75). **Ya NO ×1.5 como multiplicador de pausa (A1).** |
| Ritmo pausado | pace 0.85 (velocidad) | |
| Ritmo normal | pace 1.0 (velocidad) | |
| Ritmo rápido | pace 1.2 (velocidad) | Las pausas se acortan: base × (1/1.2) |
| Ritmo entrecortado | pace 1.0 (velocidad) | Lo entrecortado son las pausas, no el tempo |
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
| Intensidad ≥ 0.8 (escala 0–1) | +300 ms | Golpe dramático. **Ya NO "intensidad 5" (A2).** |
| Último segmento de un capítulo | +800 ms | Cola final |
| Inicio de capítulo (pause_before_ms) | 600 ms | Solo primer segmento |
| Resto de segmentos (pause_before_ms) | 0 ms | La pausa vive en pause_after_ms del anterior |

## Semántica pace / intensity (DECIDIDA 2026-11-20, no cambiar)
- **`pace` en BD = factor de VELOCIDAD float 0.6–1.3** (lento≈0.75, pausado≈0.85, normal=1.0, rápido≈1.2, entrecortado=1.0). El generador lo usa como `speed = vspeed * pace`. En `apply_emotion_pace`, un pace float multiplica las pausas por `1/pace` (hablar más lento alarga las pausas). Los nombres de ritmo también se aceptan (`PACE_MULT` en `direction/rules.py`).
- **`intensity` en BD = float 0–1.** El CSV de `aplicar_direccion.py` convierte 1–5 con `(n-1)/4`. La regla "+300 ms por intensidad alta" dispara con `intensity >= 0.8`.
- Ejemplos verificados: punto+lento+miedo = 877 ms; punto+pace 0.75+miedo = 780 ms; intensidad 1.0 → 450+300 = 750 ms; intensidad 0.5 → 450 ms.
- **Corchetes (A5):** los marcadores `[pausa=Xs]`/`[silencio=Xs]` se eliminan en `segmentize`; cualquier `[` restante es contenido legítimo (p. ej. `[risas]`) y `_concat` YA NO aborta el capítulo por él (antes lanzaba ValueError). No afirmar la "doble capa de defensa" antigua.

## Vocabularios de dirección (Espec 4.1)
- Emoción (12): neutral, alegría, tristeza, enojo, miedo, sorpresa, desprecio, ternura, orgullo, vergüenza, desesperación, determinación
- Volumen/Entrega (6): susurro, bajo, normal, firme, elevado, grito
- Ritmo (5): lento, pausado, normal, rápido, entrecortado — en BD se guardan como factor de velocidad (ver "Semántica pace / intensity")
- Intensidad: float 0–1 (CSV 1–5 → (n-1)/4)

## Defaults de volumen por verbo (Espec 4.2)
- susurró/murmuró/balbuceó/cuchicheó → susurro
- exclamó → elevado
- gritó → grito
- ordenó/advirtió/concluyó → firme
- dijo/respondió/preguntó/explicó/añadió/señaló/comentó → normal

## Decisiones tomadas
- Voz narradora por defecto: Kokoro fp16, `em_alex`.
- Un solo modelo cargado a la vez (regla AUREA) en `KokoroAdapter` (singleton).
- Metadatos en SQLite; el audio (WAV) vive en disco bajo `output/book_<id>/`.
- Dirección híbrida: reglas instantáneas para lo literal; Ollama solo como refinamiento opcional.
- XTTS reservado para voces clonadas; Kokoro para el resto.
- `direct_segment` es idempotente: re-ejecutar no duplica pausas ni corrompe datos.
- `aplicar_direccion` es idempotente: correr dos veces deja los mismos valores.
- pause_before_ms solo al inicio del capítulo (600ms); el resto va en pause_after_ms del segmento anterior.
- Marcadores `[pausa=Xs]`/`[silencio=Xs]` se limpian en `segmentize()`; un `[` restante es contenido legítimo y `_concat` ya no aborta por él (A5, 2026-11-20).
- Redondeo: truncamiento (`int()`) para coincidir con ejemplos del spec (877.5 → 877, no 878).
- 4 personajes de EL QUINTO SOL (Moctezuma, Tlilpotonqui, Iztacxochitl, Xochitl) SIN voz asignada (esperando a Alberto).
- Detector de personajes corregido (`casting/detector.py:analyze` divide por líneas en lugar de por párrafos): detecta los 7 personajes del capítulo 2 de EL QUINTO SOL (Citlali, Tochtli, Yaotl, Xochitl, Moctezuma, Tlilpotonqui, Iztacxochitl).

## Reparación 2026-11-20/21 (auditoría `~/workspace/aurea/AUDITORIA-aurea-2026-11-20.md`)
Autorizada por Alberto ("Claro", 2026-11-20). Todo verificado en la VM con BD y directorios temporales (nunca producción). Versión resultante: **0.5.0**.

### Críticos (Fase 1)
- **C1** `core/generator.py`: la primera generación ya produce el WAV final (`seg.wav_path`/`seg.status` se refrescan en memoria tras `update_segment`); el éxito solo se emite si el WAV final existe en disco; `latest.wav` se copia.
- **C2+C3** `ui/clone_panel.py` + `voice/clone_store.py`: `assign_character(cid, engine, profile_id, voice)` — XTTS sin TypeError; Kokoro GUARDA la voz elegida en BD.
- **C4** `core/database.py`: `PRAGMA foreign_keys=ON` en cada conexión + borrado manual en cascada en `delete_book`; borrar un libro deja 0 capítulos/segmentos/personajes. `Database.close()` + `transaction()` (un commit por libro, M25) + `book_by_path()` (M11, dedup de re-importación).
- **C5** `ui/main_window.py`: Pausa/Stop REALES por flag (`request_pause`/`request_resume`/`request_cancel`, comprobados entre segmentos). Stop NUNCA llama `wait()` en el hilo de UI. Pausa real (no `QThread.quit()`).
- **A8** `ui/main_window.py`: un solo guarda de trabajo en curso (`self.active_worker`); dirigir mientras se genera (y viceversa) se bloquea con mensaje.

### Semántica (Fase 2)
- **A1+A2+A3** `direction/rules.py` + `scripts/aplicar_direccion.py`: ver "Semántica pace / intensity" arriba. `PACE_SPEED` vive en `rules.py`. `apply_emotion_pace` acepta nombre o float.
- **A4** `ingestion/md_reader.py`: H2/H3/`---` antes del primer H1 no revientan; el contenido pre-H1 se conserva como capítulo propio. Tablas `|...|` omitidas, `>` de cita quitado (M13/M14). `text_reader` con fallback latin-1 (M12).

### Altos restantes (Fase 3)
- **A5** `_concat` ya no aborta por `[`; **A6** XTTS sin muestra → fallback a narradora con aviso; **A7** WAVs en PCM_16 (`sf.write` con `subtype='PCM_16'`) para que `_wav_peaks` los lea; **A9** `process_master` integrado en `_concat` (ver arriba); **A10** casilla/botón "Refinar con Ollama" conectados a `use_llm`, `MODEL` por env `AUREA_OLLAMA_MODEL` (default `qwen3.5:4b`); **A11** los 5 `.sh` de setup usan `write_once` (sin `--force` no sobrescriben archivos existentes; con `--force`, backup con timestamp); **A12** controles de Dirección leídos y aplicados al segmento seleccionado, "Aplicar voces" navega a la página Voz, papelera del sidebar borra el libro con confirmación.
- **M1/M2/M3** `direction/director.py`: is_first/prev_speaker/cola sobre el capítulo completo; idx del LLM validado (rango + emoción contra vocabulario de 12); `_chat` con timeout 120 s y cancelable; worker con flags cancel/pause y señales `log`/`cancelled`.
- **M4/M5/M6** `main_window.py`: `_refresh_chapters` conserva selección; cambio de capítulo refresca segmentos; `_direct_chapter` crea segmentos si faltan. **M7** log visible (`log_view` QTextEdit en Estudio) + señales `log` separadas de `progress`. **M8** `finally` en el generador: libera XTTS y cierra la BD del hilo. **M19** `_play` respeta el deslizador de volumen. **M29/B4** sin `_refresh_cast` duplicada ni imports sin usar; `_pending_gen` y `_volume` inicializados en `__init__`. **B12** borrar perfil pide confirmación. **B1** versión única en `aurea/__init__.py`.
- **Bugs hallados al verificar (no estaban en la auditoría):** `clone_store.clone_sample_map()` hacía `JOIN characters` sobre la BD junto al repo y reventaba con "no such table: characters" cuando esa BD no era la del proyecto — tumbaba TODA la generación. Corregido: `_db_path()` honra `AUREA_HOME` además de `AUREA_DB_PATH`, y `clone_sample_map()` devuelve `{}` si falta la tabla/columna. `database.aurea_home()` resuelve la raíz en cada llamada (el generador congelaba `AUREA_HOME` al importar y los tests escribían en `~/aurea`). `sample_prep._voices_dir()` honra `AUREA_VOICES_DIR`. El caso 3 de `verificar_pausas.py` esperaba 700 ms exactos para "..." pero el valor dirigido real es 777 ms (700 × 1/0.9, pace 0.9 por puntos suspensivos) — expectativa corregida, no el código.

### Acabado cyberpunk premium (Fase 4, solo presentación)
- `ui/theme.py`: QSS consolidado (sin duplicados M9), escala tipográfica + grid 8 px, fuentes reales verificadas con fc-list (DejaVu Sans Mono / Noto Sans Mono / Liberation Mono; Ubuntu Mono solo fallback), estados completos (QMenu, QMessageBox, popup QComboBox, botones QSpinBox, QSplitter, QTextEdit), barra de progreso legible, `HudPanel` con las 4 esquinas bracket (B9), sin emoji (glifos seguros ▶ ■ ● ◆ ▸ ✓ ✕ »), indicador de salud REAL (modelo Kokoro cargado o no).
- `ui/main_window.py`: transporte con play/pausa ▶/II, atajos Ctrl+Espacio / Espacio / S, tooltips en español. **B5: MainWindow sigue siendo QFrame** (migrar a QMainWindow no se demostró seguro; documentado en el docstring de la clase).

### Tests (Fase 5) — resultados 2026-11-21
- `tests/test_aurea.py` (nueva suite pytest): **16/16** en 2 corridas consecutivas (C1, C2, C3, C4, C5 stop, C5 pausa/reanuda, A1, A2, A3, A4 ×2, M11, M25, clone_sample_map sin tabla characters, `_voices_dir`, `_wav_peaks` PCM_16). Correr con `QT_QPA_PLATFORM=offscreen pytest tests/test_aurea.py -v`.
- `scripts/verificar_pausas.py`: **29/29** (M22: sin `or True`; caso 10 con intensidad 0–1; casos 11–12 con BD temporal propia; caso 3 corregido a 777 ms).
- `scripts/verificar_wav_capitulo.py`: **11/11** (M23: BD temporal propia, TTS falso documentado, ruta por `gen._last_wav`, `process_master` verificado por RMS 0.224 → 0.127, PCM_16, `[risas]` sobrevive).
- `scripts/verificar_direccion.py`: **todas pasan** (M22: escalas nuevas pace 0.75/1.2, intensidad 0.5/1.0/0.75; BD temporal; `NamedTemporaryFile`).
- `scripts/verificar_md.py`: **todas pasan** (ruta repo + `NamedTemporaryFile`).
- `scripts/smoke_fase4c.py`: **exit 0 con SKIP honesto** (M24: import KokoroAdapter corregido a `aurea.tts.kokoro_adapter`, API `synthesize`; perfil existente reutilizado; XTTS no disponible en la VM → clonación NO verificada aquí).
- Smoke MainWindow offscreen (`/tmp/smoke_mainwindow.py`): **17/17** (ventana, active_worker, salud, log, Ollama, volumen, pausa/stop, papelera, aplicar dirección).
- Smoke Fase 4 (`/tmp/smoke_fase4.py`): **37/37** (tema, glifos, esquinas HudPanel por píxeles, atajos, play ▶/II).
- **VM sin libEGL:** QtWidgets no importaba; se resolvió extrayendo el `.deb` oficial `libegl1` a `/tmp/egl-shim` y corriendo con `LD_LIBRARY_PATH=/tmp/egl-shim/usr/lib/x86_64-linux-gnu` (un `apt-get install` quedó bloqueado por otro proceso con el lock). Los smokes de /tmp son efímeros; la suite pytest es lo permanente.

## Resultado validado
Ver la sección "Reparación 2026-11-20/21" arriba (conteos exactos 2026-11-21). Resumen histórico previo (2026-11-12, escalas antiguas — no usar como referencia de valores):
- `scripts/verificar_pausas.py`: 20 casos en su momento; hoy 29/29 con escalas nuevas.
- `scripts/verificar_md.py`: encabezados, limpieza de formato, listas, marcadores en .md, encabezado no contamina casting, sin marcas MD en segmentos, título H1 con pausa 800ms.
- `scripts/verificar_direccion.py`: aplicar CSV a 3 segmentos, personaje no coincidente se rechaza, idempotencia, defaults por verbo susurró→susurro/gritó→grito/ordenó→firme.
- Detector de personajes probado con capítulo 2 de EL QUINTO SOL: detecta los 7 personajes (Citlali, Tochtli, Yaotl, Xochitl, Moctezuma, Tlilpotonqui, Iztacxochitl) en 23 segmentos (21 de diálogo + 2 de narración con marcadores de pausa).
- `scripts/verificar_wav_capitulo.py`: hoy 11/11 con BD temporal y TTS falso (antes: libro 20 capítulo 0 real, 92 segmentos).
- `scripts/smoke_fase4c.py`: en su momento OK con XTTS real: 11.3s de audio en 56.4s → RTF 4.98 | RAM pico 4432 MB. Hoy en la VM: SKIP honesto (sin coqui-tts).

## Problemas conocidos (audio / TTS)
- Pronunciación en español con Kokoro fp16: "baja" suena como "paja", nombres propios enredados. Limitaciones del G2P (Misaki/espeak-ng), no del código.
- `process_segment` existe pero no está conectado (ver A9 arriba): los segmentos no se procesan individualmente, solo el master final.
- 4 segmentos existentes en BD tenían `[pausa=Xs]` sin limpiar. Ya limpiados (0 restantes). **Desde 2026-11-20, `_concat` ya NO lanza ValueError por corchetes** (A5): un `[` restante es contenido legítimo.

## Siguiente paso concreto
1. ✅ Limpieza de 4 segmentos con corchetes y re-dirección capítulo 22 (libro 20) - HECHO
2. ✅ Script `scripts/verificar_wav_capitulo.py` creado y probado - HECHO
3. ✅ Reglas de pausas v2 con vocabularios espec 4.1 + marcadores `[pausa=Xs]`/`[silencio=Xs]` en cualquier texto - HECHO
4. ✅ Soporte Markdown v2 con anuncio de título H1 - HECHO
5. ✅ Sistema de dirección (`scripts/aplicar_direccion.py` + `scripts/verificar_direccion.py`) - HECHO
6. ✅ Detector de personajes corregido (divide por líneas, detecta 7 personajes del capítulo 2 de EL QUINTO SOL) - HECHO
6. 🔲 Verificar GUI v2 en pantalla real (QStackedWidget 5 secciones, transporte con WaveformView, toasts, contraste texto blanco) - PENDIENTE (usuario)
7. 🔲 Probar clonación XTTS desde la GUI: asignar perfil de voz clonada a personaje, luego generar - PENDIENTE
8. 🔲 Probar con un libro más largo y EPUB/PDF - PENDIENTE
9. 🔲 Importar guion.md de EL QUINTO SOL y verificar 6 personajes, 99 segmentos + anuncio de título - PENDIENTE (esperando archivo del usuario)
10. 🔲 Aplicar direccion-larga.csv y generar primera escena como prueba de pipeline - PENDIENTE (esperando archivo del usuario)

## OCR para PDF escaneados (v0.6.0, 2026-11-21)

**Capacidad:** importar PDF escaneados (imágenes sin capa de texto) con OCR 100% local y CPU-only. Decisión POR PÁGINA: capa de texto con >=30 caracteres y >=5 palabras -> ruta "texto"; si no, ruta OCR. El texto OCR entra al pipeline existente (segmentador → detector → generador) sin cambios en esos módulos.

**Archivos:**
- `aurea/ingestion/ocr_reader.py` (nuevo): motor OCR + limpieza quirúrgica + caché + informe. `read_pdf_ocr()` devuelve (capítulos, informe, [OcrPageResult]).
- `aurea/ingestion/pdf_reader.py`: `PdfBookReader` usa PyMuPDF por página; sin dependencias OCR conserva la ruta antigua (pypdf + ValueError ante escaneo). `scan_pdf()` = pre-escaneo rápido para la UI.
- `aurea/ingestion/import_worker.py` (nuevo): `ImportWorker` QThread (progreso por página, cancelable, pausable). Mismo guarda único `active_worker` que generador/director: OCR y TTS nunca a la vez.
- `aurea/ui/ocr_dialog.py` (nuevo): aviso "PDF escaneado detectado — OCR local (Tesseract)", selector de idioma (Español / Español+Inglés), DPI (300 por defecto).
- `aurea/ui/main_window.py`: `_import_book` pre-escanea los PDF; si hay páginas escaneadas muestra el diálogo y lanza el worker; `_finish_import` guarda el libro + confianza por página + escribe el reporte; `_show_ocr_report` muestra el resumen con botón "Abrir reporte"; botón "✕ Cancelar importación" en Biblioteca.
- `aurea/core/database.py`: migración v0.6.0 — tabla `ocr_pages(book_id, page_no, method, mean_confidence, psm, columns, needs_review)` + `set_ocr_pages()` / `ocr_pages()`. Decisión documentada: tabla propia, no columna en `chapters` (un capítulo no es una página).
- `aurea/ingestion/importer.py`: `import_book()` acepta kwargs aditivos `ocr_lang/ocr_dpi/ocr_progress/ocr_cancel/ocr_cache_root`; la interfaz anterior no cambia. El informe queda en `import_book.last_ocr_report`.
- `requirements-ocr.txt` (nuevo). Versión `0.6.0` en `aurea/__init__.py` (única constante).

**Dependencias nuevas (licencias VERIFICADAS 2026-11-21 en la VM):**
- Tesseract 5.3.4 (binario, apt): **Apache 2.0** (verificado en `/usr/share/doc/tesseract-ocr/copyright`).
- pytesseract 0.3.13: **Apache 2.0** (metadato pip; corrección: el PROMPT decía MIT, el dato verificado es Apache 2.0).
- PyMuPDF 2.1: **AGPL-3.0** (dual AGPL / comercial Artifex, metadato pip). Uso local personal sin distribución: sin problema. Si AUREA se distribuyera algún día, PyMuPDF exigiría publicar el código bajo AGPL o comprar la licencia comercial.
- Pillow 13.0.0: **MIT-CMU** (permisiva).
- Instalación en laptop: `sudo apt install tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng` + `pip install pymupdf pillow pytesseract` en el `.venv`.
- Nota VM: Pillow 13.0.0 en `~/.venv-aurea` no trae el codificador PNG (`features.check('png')` False); el fixture de tests usa JPEG. El código de producción nunca codifica PNG: sin impacto en AUREA.

**Semántica de caché:** `AUREA_HOME/ocr_cache/<sha256(pdf)[:16]>_p<NNNN>_d<DPI>_<idiomas>_t<versión Tesseract>_v<CACHE_VERSION>.json` → líneas crudas + TSV + confianza. La segunda importación del mismo PDF reutiliza la caché y no re-OCRea nada. La cancelación es reanudable por la misma vía. `CACHE_VERSION` (hoy 2) se sube si cambia el preprocesado o el OCR; invalida las entradas viejas. La limpieza (encabezados/números) corre DESPUÉS de leer la caché, así que un cambio de limpieza no necesita invalidarla.

**Limpieza quirúrgica (v1):** orden de lectura por columna (1 o 2, por posición x); PSM 6 por defecto, 4 si 2 columnas, 3 fallback (elección por página en el log); encabezados/pies repetidos en la misma zona en 3+ páginas consecutivas → eliminados y registrados; números de página (solo dígitos, 10% sup/inf) → eliminados y registrados; desguionado (continuación en minúscula); normalización (ligaduras, comillas, guiones largos a —, espacios, corchetes eliminados: ningún segmento puede contener `[`); notas al pie conservadas como párrafos normales al final de la página y registradas.

**Tres correcciones halladas con la muestra REAL (Popol Vuh, 2026-11-21):**
1. **OSD con confianza mínima (OSD_MIN_CONFIDENCE = 5.0):** Tesseract OSD dio un falso 180° en p9 (confianza 3.45) y el código la giraba boca abajo (conf media 35.4, basura total). Páginas bien detectadas: 8.98–13.42. Ahora solo se rota si confianza ≥ 5.0; si no, 0°. La p5 del fixture (giro real 180°, conf 12.21) sigue corrigiéndose.
2. **Zonas medidas sobre la imagen OCR-eada, no el render:** el recorte de márgenes del preprocesado achica la imagen; las coordenadas TSV son de la imagen recortada. `OcrPageResult.img_height` guarda ese alto y `clean_pages` lo usa por página (con fallback al alto del render).
3. **Número de página como última línea:** los escaneos traen márgenes blancos anchos y el número impreso queda a ~85% de la altura (fuera del 10% inf). Regla añadida: una línea solo-dígitos que sea la ÚLTIMA de la página también es número de página. Regex relajado a `^[\W_]*\d{1,4}[\W_]*$` (caza «: 1», «(3)», «— 10 —»). Con esto los números eliminados en la muestra pasaron de 1 a 5.
4. **PSM 4 descartado si fusiona columnas:** en el fixture, PSM 4 unía las dos columnas en una sola línea («Columna izquierda: el Columna derecha: en la»); ahora se conserva PSM 6 si PSM 4 produce líneas que cruzan la división de columnas.

**Confianza:** media por palabra (TSV conf) y por página; páginas con media <70 → `revisar`; palabras conf <40 listadas. Informe por libro en `output/ocr_<book_id>_reporte.md`.

**Limitaciones v1 (documentadas en cada informe):** tablas aplanadas fila por fila (sin reconstruir celdas); imágenes no descritas (pies de foto como texto normal); deskew solo giros de 90°/180°/270° por OSD (inclinación fina no corregida); la limpieza quirúrgica solo se aplica a páginas OCR (las de texto conservan su capa tal cual, para no cambiar la regresión de PDF con texto).

**Verificación Fase 2 preliminar (2026-11-21, VM, 300 DPI, spa+eng):**
- Fixture SINTÉTICO (`tests/test_ocr.py`, 6 págs.: 1 texto + 5 escaneadas): 5 OCR / 1 texto, conf media 95.4, 0 revisar, 5 encabezados + 5 números eliminados, 1 palabra desguionada, 2.66 s/pág. (16.0 s total); 2ª corrida 0 llamadas OCR, 5/5 caché, 0.01 s; 0 `[` en el texto; PSM 6 en la de 2 columnas (PSM 4 fusionaba); p5 deskew 180° aplicado.
- Muestra REAL Popol Vuh págs. 1–10 (`scripts/verificar_ocr_popolvuh.py`, reporte `output/ocr_popolvuh-muestra_reporte.md`): 10 OCR / 0 texto, conf media 72.7, revisar [1, 3, 4] (portada p1 46.2, portadilla p3 60.3, colofón p4 44.4; cuerpo p5–p10: 75.0–89.5), 0 encabezados/pies (esta edición no los tiene en pp. 1–10), 5 números eliminados (p2 «11», p7 «8», p8 «9», p9 «10», p10 «: 1»), 27 palabras desguionadas, 122 palabras conf <40, 5.42 s/pág. (54.2 s total, corrida en frío) → libro completo (183 págs.) estimado ~16.5 min; 2ª corrida 0.3 s (10/10 caché). 0 `[` en el texto (9,492 caracteres). Págs. a 2 columnas detectadas [1, 4] = falsos positivos (portada/colofón, no cuerpo a 2 columnas); PSM 6 en todas. **Corrección 2026-11-21 (verificación propia de Nova):** el desguionado no se reexaminaba tras unir y dejaba 7 guiones con dos cortes seguidos (p. ej. elocuen-/cia); ahora une en cadena — 27 uniones (antes 23), 0 continuaciones en minúscula sin unir; los 3 guiones restantes son continuación en mayúscula (regla: no se unen) o ruido de portada. Test de regresión `test_dehyphenation_chained` añadido.
- Suite completa: `pytest tests/test_aurea.py tests/test_ocr.py` 31/31 (16 existentes + 15 OCR, incl. el nuevo test de desguionado en cadena); `verificar_pausas/wav_capitulo/direccion/md` OK; smoke GUI offscreen OK (MainWindow + OcrOptionsDialog, spa+eng, 300 DPI).
- La verificación real del libro completo (183 págs.) espera a Alberto. Advertencia honesta: el escaneo fuente es de ~100 DPI (464×656 px por página); el cuerpo se lee bien (conf 75–90) pero con errores de OCR («mo se encore traban», «habán», «laz obras»); portada/portadilla/colofón salen mal (conf 44–60) y conviene revisarlos o excluirlos al importar.

## Reparto inteligente por género + ambientes con ducking (v0.7.0, 2026-11-21)

**Decisiones de Alberto (2026-11-20):** (1) las etiquetas Kokoro `em_*`
son masculinas y `ef_*` femeninas — el casting automático asigna según
el género del personaje; (2) formato del Popol Vuh = dramatizado
completo / teatro sonoro; (3) ambientes sintetizados APROBADOS ("si
pasa") — se mezclan bajo la narración con ducking.

### A. Reparto inteligente por género
- `aurea/casting/gender.py` (nuevo): `infer_gender(nombre, contexto)`
  -> (género, fuente). Orden: (1) léxico (español común + Popol Vuh +
  EL QUINTO SOL; "guadalupe" NO está: unisex en México, solo el
  contexto lo decide), (2) títulos don/doña/señor/señora, (3) artículos
  el/la delante del nombre en el contexto, (4) terminaciones -o/-a
  (con excepciones Ezra/Luca/Noa y Rosario/Amparo/Consuelo/Socorro).
  Sin pista -> "desconocido": NUNCA se inventa. `voice_gender(id)` lee
  el género de la voz Kokoro por su prefijo (`em_*`/`ef_*`; XTTS ->
  desconocido).
- `aurea/casting/cast_manager.py`: `build_cast(book_id, names, texts)`
  (los textos son opcionales, para el contexto); masculino -> `em_santa`,
  femenino -> `ef_dora`, desconocido -> rotación de las voces por
  defecto (nunca `em_alex`, reservado al narrador). Solo hay UNA voz
  Kokoro en español por género además del narrador: al repetir voz, la
  velocidad rota 1.0/0.95/1.05 para diferenciar personajes. Reglas:
  nunca `ef_*` a un masculino ni `em_*` a una femenina; lo existente
  NO se reasigna solo (un personaje viejo sin género lo recibe inferido,
  con su voz intacta); `set_gender()` (corrección manual) marca
  `gender_manual=1` y CONSERVA la voz. `cast_info()` devuelve por
  personaje género, género de voz y `mismatch` (voz del otro género).
- BD (migración v0.7.0, automática): `characters.gender` /
  `characters.gender_manual`; `Character` en `core/models.py` igual.
- UI (`ui/main_window.py` + `ui/gender_dialog.py` nuevo): el reparto
  muestra `Nombre [M/F/?] → voz (M/F)` con marcas "◆ sin confirmar" y
  "◆ voz no encaja"; botón "✎ Corregir género" abre el diálogo (un
  combo por personaje; la voz no cambia al corregir el género).
- El detector NO se tocó: no guarda género; `build_cast` recibe los
  textos del libro como contexto (cambio mínimo).

### B. Ambientes con ducking
- `aurea/core/ambience.py` (nuevo): generadores procedurales adaptados
  de `~/workspace/aurea-ambientes/generar_ambientes.py` (viento, lluvia,
  fuego, selva de noche), ahora con `sr` paramétrico y sin fades en el
  bucle. Sin modelos, sin nube, CPU-only, numpy+soundfile (ya eran
  dependencias; NADA nuevo que licenciar).
- Caché: `BedCache` genera un bucle de 60 s por ambiente en
  `AUREA_HOME/ambience_cache/bed_<nombre>_<sr>hz_v1.wav` (semillas
  fijas: la misma cama suena igual siempre); la 2ª generación lo lee
  de disco sin regenerar. `tile_bed` repite el bucle con crossfade de
  1 s en las juntas (sin clics).
- Mezcla: `mix_ambience(narración, cama, sr, bed_level=0.25,
  duck_level=0.30, fade_s=2.0)`. La cama suena al 25% en los silencios
  y baja al 30% de su nivel (7.5% absoluto) mientras hay voz
  (envolvente RMS por ventanas de 50 ms, ataque 80 ms, liberación
  300 ms). Fades lineales de 2 s SOLO sobre la cama al inicio/fin del
  capítulo. Si la suma supera 0.99 se escala todo (no se recorta).
- Integración mínima en `core/generator.py`: `_concat(..., ambience,
  bed_level)` mezcla ANTES de `process_master` (el master normaliza el
  conjunto y conserva la relación voz/cama); un fallo de mezcla nunca
  mata el capítulo (mismo patrón try/except que A9). `ChapterGenerator`
  acepta `ambience`/`bed_level` o los lee del capítulo. TTS,
  segmentación, detector y director INTACTOS.
- BD (migración v0.7.0): `chapters.ambience` (default 'none') /
  `chapters.bed_level` (default 0.25); `Chapter` en `core/models.py`
  igual; `db.set_chapter_ambience()`.
- UI (Estudio): combo "Ambiente" (Sin ambiente / Viento / Lluvia /
  Fuego / Selva de noche — recomendado para el Popol Vuh) + nivel de
  cama 0.05–0.60 (defecto 0.25). Se guarda por capítulo y se carga al
  cambiar de capítulo; se aplica en la próxima generación.
- Recomendación documentada: selva de noche para el Popol Vuh
  (grillos + viento suave + zumbido grave).

### Tests v0.7.0 (2026-11-21, VM)
- `tests/test_v070.py` (nuevo): **18/18** (léxico Popol Vuh/Quinto
  Sol; títulos/artículos/terminaciones; desconocido nunca inventado;
  prefijo de voz; pick_voice sin cruces + rotación de velocidad;
  build_cast por género; personaje viejo gana género sin perder voz;
  corrección manual conserva voz y bloquea re-inferencia; nombres
  duplicados no revientan; mismatch;
  caché genera una vez; tile sin salto en juntas; ducking por RMS
  voz<0.6×pausa; envolvente; fades; mix none passthrough; ambiente
  persistido; _concat con ambiente produce WAV con cama en la pausa).
- Suite completa: `pytest tests/test_aurea.py tests/test_ocr.py
  tests/test_v070.py` **49/49 en 2 corridas consecutivas** (16+15+18).
- `verificar_pausas/wav_capitulo/direccion/md`: TODAS PASAN.
- Smoke GUI offscreen (`/tmp/smoke_v070.py`, efímero): **20/20**
  (ventana 0.7.0, combo ambiente 5 opciones, reparto [M]/[F]/[?],
  diálogo de género, ambiente guardado/recargado, generación completa
  con selva de noche -> WAV estéreo final).
- Medido: la cama de selva de noche en pausa queda en RMS ~0.004 tras
  el master (tenue, por diseño: grillos dispersos + cama al 25%).
- Sin verificar en VM: voces Kokoro reales, XTTS real, aspecto visual
  de la GUI y el sonido real de la mezcla (solo en la laptop de
  Alberto, con sus oídos).

## Regla de trabajo (compacción)
En cada pausa natural, revisar el consumo de contexto. Si supera ~70 %, escribir/actualizar `STATE.md` antes de continuar: qué se construye, qué está hecho (rutas exactas), qué falta, decisiones y siguiente paso. Al iniciar o reanudar, leer primero `STATE.md`; si no existe, partir de cero sin asumir progreso previo (no es error).