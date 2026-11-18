# AUREA Audiobook Studio

Estudio local de audiolibros dramatizados. 100% offline tras la instalación.

## Estado: Fase 4b + 4c — Director Híbrido y clonación de voz
- Importación TXT, EPUB y PDF (con capa de texto)
- Biblioteca, capítulos y segmentos en SQLite
- Voz del narrador: Kokoro fp16 (em_alex, ef_dora, em_santa)
- Generación por lotes en segundo plano, con caché y reanudación
- Reproducción del capítulo dentro de la app
- 🎭 Reparto dramatizado: detección de diálogos y personajes por reglas, perfiles vocales persistentes, voz por personaje
- 🎬 Director Híbrido: dirección instantánea por reglas (susurro/grito/ritmo/pausas) + refinamiento opcional con Ollama solo para narración ambigua
- 🗣️ Clonación de voz: XTTS v2 con perfiles de muestra (importar, analizar, asignar a personajes)

## Uso
    cd ~/aurea
    source .venv/bin/activate
    python -m aurea.main

  1) Importa un TXT/EPUB/PDF (botón 📚)
  2) Selecciona el libro → 🎭 Detectar reparto → revisa/asigna voces (🎙️)
  3) 🎬 Dirigir capítulo (instantáneo; casilla Ollama opcional para matiz extra)
  4) ▶ Generar capítulo → 🔊 Escuchar

## Clonación de voz (Fase 4c)
    python scripts/smoke_fase4c.py   # valida XTTS con un perfil existente
Requiere: PyTorch CPU, Torchaudio y coqui-tts[codec] instalados en .venv.
# Aurea
