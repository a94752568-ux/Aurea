"""Prueba de humo Fase 4c: crea un perfil desde un WAV sintético de Kokoro,
genera una frase clonada y mide RTF/RAM."""
import os, sys, time, resource
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from aurea.voice import sample_prep, clone_store
from aurea.voice.xtts_adapter import XTTSAdapter

os.makedirs("output", exist_ok=True)

# 1) Perfil de prueba: si no hay muestras reales, se usa una voz Kokoro exportada
profiles = clone_store.list_profiles()
if profiles:
    pid = profiles[0]["id"]; name = profiles[0]["name"]
    src = profiles[0]["sample_path"]
    print(f"Perfil existente: {name}")
else:
    # genera 8 s de voz Kokoro como muestra fuente (si el motor base está disponible)
    try:
        from aurea.core.tts import KokoroAdapter
        k = KokoroAdapter().load()
        audio, sr = k.generate("Esta es una muestra de prueba para el laboratorio de voces clonadas de Aurea.",
                               voice="em_alex")
        src = "output/_muestra_prueba.wav"
        import soundfile as sf; sf.write(src, audio, sr)
    except Exception:
        # último recurso: tono modulado (solo valida el pipeline, no la calidad)
        sr = 22050; t = np.linspace(0, 8, sr*8)
        audio = (0.3*np.sin(2*np.pi*180*t*(1+0.2*np.sin(2*np.pi*0.5*t)))).astype(np.float32)
        src = "output/_muestra_prueba.wav"
        import soundfile as sf; sf.write(src, audio, sr)
    pid, report = sample_prep.import_sample(src, "prueba_fase4c")
    print("Informe:", report)

# 2) Generación clonada
p = clone_store.get_profile(pid)
xtts = XTTSAdapter()
texto = "El viejo faro resistía estoico el embate de las olas, como lo había hecho por siglos."
t0 = time.time()
audio, sr = xtts.generate(texto, speaker_wav=p["sample_path"], speed=1.0,
                          out_path="output/smoke_fase4c.wav")
dt = time.time() - t0
dur = len(audio) / sr
ram = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"OK: {dur:.1f}s de audio en {dt:.1f}s → RTF {dt/max(dur,0.01):.2f} | RAM pico {ram:.0f} MB")
print("Archivo: output/smoke_fase4c.wav")
