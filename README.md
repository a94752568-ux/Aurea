# AUREA Audiobook Studio

Estudio local de audiolibros dramatizados. 100% offline tras la instalación.

## Escritorio Electron (rama `electron-refactor`)

Nueva capa de presentación con acabado Neon HUD, sobre el mismo motor local:

    git checkout electron-refactor
    bash scripts/setup_electron.sh   # npm install + verificación del server
    bash aurea-electron.sh           # arranca Electron + servidor Python

- **Arquitectura**: Electron (UI web premium) ↔ servidor HTTP local (`aurea/server.py`, stdlib, sin dependencias nuevas) ↔ motor Python sin Qt (`core/engine.py`, `direction/engine.py`).
- La app PySide6 clásica sigue funcionando: `core/generator.py` y `direction/director.py` son ahora adaptadores que delegan en el motor Qt-free.
- Un solo job TTS a la vez (regla AUREA), progreso y log vía `/api/job`, waveform por `/api/audio/peaks`.

## Estado: Fase 4b + 4c — Director Híbrido y clonación de voz
- Importación TXT, Markdown, EPUB y PDF (con capa de texto)
- Biblioteca, capítulos y segmentos en SQLite
- Voz del narrador: Kokoro fp16 (em_alex, ef_dora, em_santa)
- Generación por lotes en segundo plano, con caché y reanudación
- Reproducción del capítulo dentro de la app
- 🎭 Reparto dramatizado: detección de diálogos y personajes por reglas, perfiles vocales persistentes, voz por personaje
- 🎬 Director Híbrido: dirección instantánea por reglas (susurro/grito/ritmo/pausas) + refinamiento opcional con Ollama solo para narración ambigua
- 🗣️ Clonación de voz: XTTS v2 con perfiles de muestra (importar, analizar, asignar a personajes)

## Uso (app PySide6 clásica)
    cd ~/aurea
    source .venv/bin/activate
    python -m aurea.main

  1) Importa un TXT/MD/EPUB/PDF (botón 📚)
  2) Selecciona el libro → 🎭 Detectar reparto → revisa/asigna voces (🎙️)
  3) 🎬 Dirigir capítulo (instantáneo; casilla Ollama opcional)
  4) ▶ Generar capítulo → 🔊 Escuchar

## Clonación de voz (Fase 4c)
    python scripts/smoke_fase4c.py   # valida XTTS con un perfil existente
Requiere: PyTorch CPU, Torchaudio y coqui-tts[codec] instalados en .venv.
