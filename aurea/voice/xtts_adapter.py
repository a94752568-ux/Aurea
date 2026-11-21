"""Adaptador XTTS-v2 (clonación, CPU). Mismo contrato que KokoroAdapter:
cargar (lazy), generar, cancelar, liberar."""
import os, threading, time
import numpy as np
import soundfile as sf

from .pronunciation import prepare_text_for_tts

class XTTSAdapter:
    SAMPLE_RATE = 24000

    def __init__(self):
        self._tts = None
        self._lock = threading.Lock()
        self._cancel = False

    # ---------- contrato ----------
    def load(self):
        with self._lock:
            if self._tts is None:
                from TTS.api import TTS  # coqui-tts
                self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
        return self

    def generate(self, text, speaker_wav, speed=1.0, out_path=None, language="es",
                 log=None):
        """Genera audio clonado. Devuelve (audio ndarray float32, sr).

        v0.7.2: el texto se prepara SOLO para sintetizar (léxico +
        números en palabras); la BD y el guion no se tocan.
        """
        self.load()
        self._cancel = False
        text, _subs = prepare_text_for_tts(text, log=log)
        t0 = time.time()
        with self._lock:
            if self._cancel:
                return None, self.SAMPLE_RATE
            wav = self._tts.tts(text=text, speaker_wav=speaker_wav,
                                language=language, speed=speed)
        audio = np.asarray(wav, dtype=np.float32)
        if out_path:
            sf.write(out_path, audio, self.SAMPLE_RATE, subtype="PCM_16")  # A7
        return audio, self.SAMPLE_RATE

    def cancel(self):
        self._cancel = True

    def release(self):
        with self._lock:
            self._tts = None

    # ---------- utilidades ----------
    @staticmethod
    def apply_intensity(audio, intensity=1.0):
        """Dirección sin timbre emocional: ganancia acotada (evita clip)."""
        g = max(0.2, min(1.6, float(intensity)))
        out = audio * g
        peak = np.max(np.abs(out)) or 1.0
        if peak > 0.98:
            out = out * (0.98 / peak)
        return out

    @staticmethod
    def silence(seconds):
        return np.zeros(int(seconds * 24000), dtype=np.float32)
