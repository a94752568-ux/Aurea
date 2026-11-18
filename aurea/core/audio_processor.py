"""Procesamiento de audio automático para uniformizar el volumen y evitar picos.

Aplica, por segmento y en master final:
  1. Normalización RMS (todos los segmentos a mismo nivel percibido)
  2. Compresión de rango dinámico (reduce diferencia fuerte/débil)
  3. Limitador (evita picos que saturen / clipping)
  4. Potenciador de graves (bass boost suave para calidez)
  5. Herramientas estéreo (consistencia mono/estéreo, ancho controlado)

Todo implementado con numpy + soundfile (sin dependencias nuevas).
"""
import numpy as np


class AudioProcessor:
    """Procesador de audio para uniformizar volumen y evitar picos."""

    # --- parámetros por defecto -------------------------------------------
    TARGET_RMS = 0.12          # nivel RMS objetivo (lineal, 0..1)
    COMPRESSOR_THRESHOLD = 0.25  # umbral de compresión (lineal)
    COMPRESSOR_RATIO = 4.0         # ratio de compresión
    LIMITER_CEILING = 0.90         # techo del limitador (evita clipping)
    BASS_BOOST_DB = 2.5            # ganancia de graves en dB
    BASS_CUTOFF_HZ = 180           # frecuencia de corte del filtro de graves
    STEREO_WIDTH = 1.0             # 1.0 = sin cambio, >1 = más ancho

    def __init__(self, sr=24000):
        self.sr = sr

    # ------------------------------------------------------------------ #
    # 1. Normalización RMS
    # ------------------------------------------------------------------ #
    def normalize_rms(self, samples, target_rms=None):
        """Escala los samples para que su RMS sea target_rms."""
        if target_rms is None:
            target_rms = self.TARGET_RMS
        samples = np.asarray(samples, dtype=np.float32)
        rms = np.sqrt(np.mean(samples ** 2) + 1e-9)
        if rms < 1e-6:
            return samples
        gain = target_rms / rms
        # limitar ganancia para no amplificar demasiado silencio casi-vacío
        gain = min(gain, 8.0)
        return samples * gain

    # ------------------------------------------------------------------ #
    # 2. Compresor de rango dinámico
    # ------------------------------------------------------------------ #
    def compress(self, samples, threshold=None, ratio=None):
        """Compresor simple por muestra (feed-forward, sin attack/release complejos).

        Aplica reducción de ganancia solo sobre muestras que superan el umbral.
        """
        if threshold is None:
            threshold = self.COMPRESSOR_THRESHOLD
        if ratio is None:
            ratio = self.COMPRESSOR_RATIO
        samples = np.asarray(samples, dtype=np.float32)
        abs_samples = np.abs(samples)
        # reducción de ganancia donde excede el umbral
        reduction = np.ones_like(abs_samples)
        over = abs_samples > threshold
        if np.any(over):
            # ganancia comprimida: threshold + (x - threshold) / ratio
            compressed = threshold + (abs_samples[over] - threshold) / ratio
            reduction[over] = compressed / abs_samples[over]
        return samples * reduction

    # ------------------------------------------------------------------ #
    # 3. Limitador (evita clipping)
    # ------------------------------------------------------------------ #
    def limit(self, samples, ceiling=None):
        """Limitador suave: recorta picos por encima del ceiling."""
        if ceiling is None:
            ceiling = self.LIMITER_CEILING
        samples = np.asarray(samples, dtype=np.float32)
        return np.clip(samples, -ceiling, ceiling)

    # ------------------------------------------------------------------ #
    # 4. Potenciador de graves
    # ------------------------------------------------------------------ #
    def bass_boost(self, samples, gain_db=None, cutoff_hz=None):
        """Filtro de graves simple (uno-pole low-pass) mezclado con ganancia.

        Añade calidez sin distorsionar. gain_db > 0 refuerza graves.
        """
        if gain_db is None:
            gain_db = self.BASS_BOOST_DB
        if cutoff_hz is None:
            cutoff_hz = self.BASS_CUTOFF_HZ
        samples = np.asarray(samples, dtype=np.float32)
        # coeficiente del filtro low-pass simple (uno-pole)
        fc = cutoff_hz / self.sr
        rc = 1.0 / (2.0 * np.pi * fc)
        dt = 1.0 / self.sr
        alpha = dt / (rc + dt)
        # aplicar filtro low-pass (solo graves)
        bass = np.zeros_like(samples)
        bass[0] = samples[0]
        for i in range(1, len(samples)):
            bass[i] = bass[i - 1] + alpha * (samples[i] - bass[i - 1])
        # mezclar: original + graves reforzados
        gain_linear = 10.0 ** (gain_db / 20.0)
        boosted = samples + (bass - samples) * (gain_linear - 1.0) * 0.5
        return boosted

    # ------------------------------------------------------------------ #
    # 5. Herramientas estéreo
    # ------------------------------------------------------------------ #
    def to_stereo(self, samples):
        """Asegura que el audio sea estéreo (2 canales). Si es mono, duplica."""
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim == 1:
            return np.stack([samples, samples], axis=1)
        return samples

    def stereo_width(self, samples, width=None):
        """Ajusta el ancho estéreo mediante procesamiento Mid/Side.

        width=1.0 sin cambio; >1.0 más ancho; <1.0 más mono.
        """
        if width is None:
            width = self.STEREO_WIDTH
        samples = self.to_stereo(samples)
        left = samples[:, 0]
        right = samples[:, 1]
        mid = (left + right) * 0.5
        side = (left - right) * 0.5
        side *= width
        new_left = mid + side
        new_right = mid - side
        return np.stack([new_left, new_right], axis=1)

    # ------------------------------------------------------------------ #
    # Pipelines completos
    # ------------------------------------------------------------------ #
    def process_segment(self, samples, sr=None):
        """Pipeline completo para un segmento individual.

        Orden: normalizar RMS -> comprimir -> limitar -> graves -> estéreo.
        """
        if sr is not None and sr != self.sr:
            # re-muestreo simple por omisión (igual tasa esperada)
            pass
        samples = self.normalize_rms(samples)
        samples = self.compress(samples)
        samples = self.limit(samples)
        samples = self.bass_boost(samples)
        samples = self.stereo_width(samples)
        return samples

    def process_master(self, samples, sr=None):
        """Pipeline master para el capítulo final (más suave, sin over-process).

        Orden: normalizar RMS ligero -> comprimir suave -> limitar -> graves suaves.
        """
        samples = self.normalize_rms(samples, target_rms=0.14)
        samples = self.compress(samples, threshold=0.30, ratio=3.0)
        samples = self.limit(samples, ceiling=0.92)
        samples = self.bass_boost(samples, gain_db=1.5, cutoff_hz=150)
        samples = self.stereo_width(samples, width=1.05)
        return samples
