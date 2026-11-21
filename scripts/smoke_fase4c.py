"""Prueba de humo Fase 4c: crea un perfil desde un WAV, genera una frase
clonada con XTTS y mide RTF/RAM.

M24: el import de KokoroAdapter era erróneo (`aurea.core.tts`, no
existe) y el método era `generate()` (la API real es `synthesize()`).
Además, re-ejecutar el smoke duplicaba el perfil "prueba_fase4c"
(INSERT sin dedup) y escribía en ./output del cwd.

Reglas de este smoke:
- Todo en directorios temporales (AUREA_HOME + AUREA_VOICES_DIR):
  nunca toca la BD ni las voces de producción.
- Si el perfil "prueba_fase4c" ya existe, se REUTILIZA (no se duplica).
- La muestra fuente usa Kokoro real si hay modelos; si no, un tono
  modulado (solo valida el pipeline, no la calidad — se dice claro).
- XTTS real: si coqui-tts no está instalado (VM), el paso de clonación
  se marca SKIP honesto y el smoke termina en 0 SIN afirmar clonación.
  En la laptop, con XTTS instalado, la clonación se verifica de verdad.
"""
import os
import resource
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="aurea-smoke-4c-")
os.environ["AUREA_HOME"] = _TMP
os.environ["AUREA_VOICES_DIR"] = os.path.join(_TMP, "voices")
os.environ["AUREA_DB_PATH"] = os.path.join(_TMP, "aurea.db")
OUT = os.path.join(_TMP, "output")
os.makedirs(OUT, exist_ok=True)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from aurea.voice import clone_store, sample_prep  # noqa: E402

PROFILE_NAME = "prueba_fase4c"


def existing_profile(name):
    for p in clone_store.list_profiles():
        if p["name"] == name:
            return p
    return None


# 1) Perfil de prueba: reutilizar si existe, si no crearlo.
prof = existing_profile(PROFILE_NAME)
if prof:
    pid, src = prof["id"], prof["sample_path"]
    print(f"Perfil existente reutilizado: {prof['name']} (id={pid})")
else:
    src = os.path.join(OUT, "_muestra_prueba.wav")
    # M24: import correcto (aurea.tts.kokoro_adapter) y API real
    # (load + synthesize, no generate).
    try:
        from aurea.tts.kokoro_adapter import KokoroAdapter
        k = KokoroAdapter().load()
        audio, sr = k.synthesize(
            "Esta es una muestra de prueba para el laboratorio de voces "
            "clonadas de Aurea.", voice="em_alex")
        sf.write(src, audio, sr)
        print("Muestra fuente: Kokoro real (em_alex)")
    except Exception as e:  # noqa: BLE001 - sin modelos en la VM
        print(f"Muestra fuente: tono modulado (Kokoro no disponible: {e})")
        sr = 22050
        t = np.linspace(0, 8, sr * 8)
        audio = (0.3 * np.sin(2 * np.pi * 180 * t
                               * (1 + 0.2 * np.sin(2 * np.pi * 0.5 * t)))
                 ).astype(np.float32)
        sf.write(src, audio, sr)
    pid, report = sample_prep.import_sample(src, PROFILE_NAME)
    print("Informe:", report)

# 2) Generación clonada (solo con XTTS real).
p = clone_store.get_profile(pid)
print(f"Perfil: {p['name']} -> {p['sample_path']}")
try:
    from aurea.voice.xtts_adapter import XTTSAdapter
    xtts = XTTSAdapter()
    xtts.load()  # ImportError si coqui-tts no está instalado
except Exception as e:  # noqa: BLE001
    print(f"SKIP: XTTS no disponible en esta máquina ({e}).")
    print("La clonación real NO se verificó aquí; el resto del pipeline "
          "(muestra + perfil) sí pasó.")
    print(f"Artefactos temporales en {_TMP}")
    sys.exit(0)

texto = ("El viejo faro resistía estoico el embate de las olas, "
         "como lo había hecho por siglos.")
t0 = time.time()
out_path = os.path.join(OUT, "smoke_fase4c.wav")
audio, sr = xtts.generate(texto, speaker_wav=p["sample_path"], speed=1.0,
                          out_path=out_path)
dt = time.time() - t0
dur = len(audio) / sr
ram = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"OK: {dur:.1f}s de audio en {dt:.1f}s → RTF {dt / max(dur, 0.01):.2f} "
      f"| RAM pico {ram:.0f} MB")
print(f"Archivo: {out_path}")
