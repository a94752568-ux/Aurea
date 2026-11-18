# AUREA — Estado compactado (STATE.md)

Generado: 2026-11-12. Actualizado tras implementar mejoras para EL QUINTO SOL (reglas de pausas v2, soporte Markdown v2, sistema de dirección, clonación XTTS integrada).
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
- Procesamiento de audio automático: `core/audio_processor.py` (normalización RMS, compresión, limitador, potenciador de graves, herramientas estéreo), integrado en `core/generator.py` (`process_segment` por segmento; `process_master` en `_concat`).
- Tema Cyberpunk Neon para la GUI: `ui/theme.py` (paleta COLORS cian #00f3ff + magenta #ff00ff + texto #e8fbff + fondo #080c18, QSS global con bordes de color, `apply_dark_palette()` para QPalette Fusion oscura, `HudPanel` con esquinas bracket, `Sidebar`, `WaveformView`, `toast`). Integrado en `main_window.py` vía `apply_theme` + `apply_dark_palette`.
- Flujo dirigir-antes-de-generar: `main_window.py:_generate_chapter_with_direction()` dirige automáticamente segmentos no dirigidos antes de generar audio. Botón "🎬 Dirigir" en página Estudio. `_ensure_segments()` crea segmentos automáticamente al importar un capítulo vacío. `DirectionWorker` + `ChapterGenerator` encadenados. Feedback con QMessageBox en todos los puntos de fallo. Primer capítulo seleccionado automáticamente al importar.
- Clonación XTTS en reparto: `casting/cast_manager.py` (`build_cast` asigna voces Kokoro por defecto `ef_dora`/`em_santa`; las voces clonadas XTTS se asignan desde la GUI). `voice_map` devuelve `(engine, voz, velocidad)`. `ui/clone_panel.py` (flujo simplificado: importar → asignar, con selector Kokoro/XTTS). `ui/main_window.py` (página Reparto muestra voces asignadas).

Archivos fuente clave (`/home/aegis2/aurea/aurea/`):
- `__init__.py` (versión `0.4b+4c`)
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
- Un solo modelo cargado a la vez (regla AUREA) en `KokoroAdapter` (singleton).
- Metadatos en SQLite; el audio (WAV) vive en disco bajo `output/book_<id>/`.
- Dirección híbrida: reglas instantáneas para lo literal; Ollama solo como refinamiento opcional.
- XTTS reservado para voces clonadas; Kokoro para el resto.
- `direct_segment` es idempotente: re-ejecutar no duplica pausas ni corrompe datos.
- `aplicar_direccion` es idempotente: correr dos veces deja los mismos valores.
- pause_before_ms solo al inicio del capítulo (600ms); el resto va en pause_after_ms del segmento anterior.
- Marcadores `[` nunca llegan al TTS: dos capas de defensa (limpieza en `segmentize()` + ValueError en `_concat()`).
- Redondeo: truncamiento (`int()`) para coincidir con ejemplos del spec (877.5 → 877, no 878).
- 4 personajes de EL QUINTO SOL (Moctezuma, Tlilpotonqui, Iztacxochitl, Xochitl) SIN voz asignada (esperando a Alberto).
- Detector de personajes corregido (`casting/detector.py:analyze` divide por líneas en lugar de por párrafos): detecta los 7 personajes del capítulo 2 de EL QUINTO SOL (Citlali, Tochtli, Yaotl, Xochitl, Moctezuma, Tlilpotonqui, Iztacxochitl).

## Resultado validado
- `scripts/verificar_pausas.py`: TODAS LAS PRUEBAS PASAN (20 casos: puntuación, cambio de hablante, marcadores al final/en medio/fuera de rango, modificadores ritmo/emoción, pregunta +200ms, intensidad +300ms, idempotencia, no-NULL).
- `scripts/verificar_md.py`: TODAS LAS PRUEBAS PASAN (encabezados, limpieza de formato, listas, marcadores en .md, encabezado no contamina casting, sin marcas MD en segmentos, título H1 con pausa 800ms).
- `scripts/verificar_direccion.py`: TODAS LAS PRUEBAS PASAN (aplicar CSV a 3 segmentos, personaje no coincidente se rechaza, idempotencia, defaults por verbo susurró→susurro/gritó→grito/ordenó→firme).
- Detector de personajes probado con capítulo 2 de EL QUINTO SOL: detecta los 7 personajes (Citlali, Tochtli, Yaotl, Xochitl, Moctezuma, Tlilpotonqui, Iztacxochitl) en 23 segmentos (21 de diálogo + 2 de narración con marcadores de pausa).
- `scripts/verificar_wav_capitulo.py`: verifica duración WAV = suma segmentos + suma pausas, sin clipping, sin corchetes en texto. Probado con libro 20 capítulo 0 (92 segmentos, 26 con audio generado): _concat funciona correctamente, sin clipping (peak 0.3872), sin corchetes, todos con pause_after_ms.
- `scripts/smoke_fase4c.py`: OK: 11.3s de audio en 56.4s → RTF 4.98 | RAM pico 4432 MB.

## Problemas conocidos (audio / TTS)
- Pronunciación en español con Kokoro fp16: "baja" suena como "paja", nombres propios enredados. Limitaciones del G2P (Misaki/espeak-ng), no del código.
- 4 segmentos existentes en BD tenían `[pausa=Xs]` sin limpiar. Ya limpiados (0 restantes). `_concat` lanza ValueError si intenta sintetizarlos (segunda capa de defensa). Los nuevos segmentos desde `segmentize()` tienen texto limpio.

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

## Regla de trabajo (compacción)
En cada pausa natural, revisar el consumo de contexto. Si supera ~70 %, escribir/actualizar `STATE.md` antes de continuar: qué se construye, qué está hecho (rutas exactas), qué falta, decisiones y siguiente paso. Al iniciar o reanudar, leer primero `STATE.md`; si no existe, partir de cero sin asumir progreso previo (no es error).