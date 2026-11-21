import gc
import threading
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro
from misaki import espeak
from misaki.espeak import EspeakG2P

MODELS_DIR = Path.home() / "aurea" / "models"
VOICES_ES = ["em_alex", "ef_dora", "em_santa"]  # las 3 voces en español de Kokoro


class KokoroAdapter:
    """Adaptador del motor de voz. Un solo modelo cargado a la vez (regla AUREA)."""

    _lock = threading.Lock()
    _instance = None

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._kokoro = None
                inst._model_file = None
                inst._g2p = EspeakG2P(language="es")
                inst._fallback = espeak.EspeakFallback(british=False)
                cls._instance = inst
        return cls._instance

    @property
    def loaded(self):
        return self._kokoro is not None

    def load(self, model_file="kokoro-v1.0.fp16.onnx"):
        if self._kokoro is not None and self._model_file == model_file:
            return
        self.free()
        model = MODELS_DIR / model_file
        voices = MODELS_DIR / "voices-v1.0.bin"
        if not model.exists() or not voices.exists():
            raise FileNotFoundError(
                "Faltan modelos en ~/aurea/models — corre el Voice Lab (Fase 1) primero.")
        self._kokoro = Kokoro(str(model), str(voices))
        self._model_file = model_file

    def synthesize(self, text, voice="em_alex", speed=1.0):
        self.load()
        phonemes, _ = self._g2p(text)
        return self._kokoro.create(phonemes, voice, speed=speed, is_phonemes=True)

    @staticmethod
    def save_wav(path, samples, sr):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # A7: PCM_16 siempre. Los WAV float32 no los leía _wav_peaks
        # (exige 16-bit) y la forma de onda nunca se dibujaba.
        sf.write(str(path), samples, sr, subtype="PCM_16")

    def free(self):
        self._kokoro = None
        self._model_file = None
        gc.collect()
