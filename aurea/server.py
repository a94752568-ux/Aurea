#!/usr/bin/env python3
"""Servidor puente AUREA (refactor Electron).

Expone el motor local como API JSON en 127.0.0.1 para el escritorio
Electron. Estándar de la librería: sin PySide6 (cero Qt) ni dependencias
nuevas, sobre el mismo motor validado de las Fases 1-4c.

Arranque: .venv/bin/python aurea/server.py
Regla AUREA respetada: un solo job TTS/dirección a la vez (JobManager).

v0.6: POST /api/chapters/{id}/clean (fuera etiquetas de habla "—dijo
Narrador." de los segmentos ya guardados, invalida su WAV) y
POST /api/books/{id}/cast-sanitize (reconciliación manual de voces).
"""
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurea.core.database import Database          # noqa: E402
from aurea.core.engine import assemble_chapter, run_generation  # noqa: E402
from aurea.core.segmentation import clean_segment_text, segmentize  # noqa: E402
from aurea.direction.engine import run_direction  # noqa: E402
from aurea.casting.cast_manager import CastManager    # noqa: E402
from aurea.casting.detector import detect_characters  # noqa: E402
from aurea.ingestion.importer import import_book      # noqa: E402
from aurea.tts.kokoro_adapter import VOICES_ES        # noqa: E402
from aurea.voice import clone_store                   # noqa: E402
from aurea.voice.sample_prep import import_sample     # noqa: E402

import numpy as np       # noqa: E402
import soundfile as sf   # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("AUREA_PORT", "8765"))
OUTPUT_ROOT = (Path.home() / "aurea" / "output").resolve()
KOKORO_VOICES = ["em_alex", "ef_dora", "em_santa"]

# ------------------------------------------------------------------
# JobManager: un solo trabajo TTS/dirección a la vez (regla AUREA)
# ------------------------------------------------------------------
_job_lock = threading.Lock()
_job = {"kind": None, "chapter_id": None, "state": "idle",
        "progress": 0, "message": "", "log": []}


def _log(msg):
    with _job_lock:
        _job["log"] = (_job["log"] + [str(msg)])[-200:]


def _start_job(kind, chapter_id, fn):
    """Lanza fn(on_progress, on_done, on_failed) en un hilo de trabajo.
    Devuelve False si ya hay un trabajo en curso."""
    with _job_lock:
        if _job["state"] == "running":
            return False
        _job.update(kind=kind, chapter_id=chapter_id, state="running",
                    progress=0, message="", log=[])

    def on_progress(pct, msg):
        with _job_lock:
            _job["progress"] = int(pct or 0)
            _job["message"] = str(msg or "")
        if msg:
            _log(msg)

    def on_failed(msg):
        with _job_lock:
            _job["state"] = "failed"
            _job["message"] = str(msg)
        _log("✖ " + str(msg))

    def on_done(*args):
        line = " · ".join(str(a) for a in args if a)
        with _job_lock:
            _job["state"] = "done"
            _job["progress"] = 100
            _job["message"] = line
        _log("✔ " + line)

    def runner():
        try:
            fn(on_progress, on_done, on_failed)
        except Exception as e:  # noqa: BLE001
            on_failed(f"{type(e).__name__}: {e}")
            _log(traceback.format_exc()[-500:])

    threading.Thread(target=runner, daemon=True).start()
    return True


# ------------------------------------------------------------------
# Serialización
# ------------------------------------------------------------------
def _book_dict(b):
    return {"id": b.id, "title": b.title, "path": b.path, "fmt": b.fmt}


def _char_dict(c):
    return {"id": c.id, "book_id": c.book_id, "name": c.name,
            "voice": c.voice, "speed": c.speed,
            "is_narrator": c.is_narrator, "engine": c.engine,
            "voice_profile_id": c.voice_profile_id}


def _seg_dict(s, cmap=None):
    has_wav = bool(s.wav_path) and Path(s.wav_path).exists()
    d = {"id": s.id, "chapter_id": s.chapter_id, "idx": s.idx,
         "text": s.text, "status": s.status, "kind": s.kind,
         "character_id": s.character_id,
         "character_name": (cmap or {}).get(s.character_id),
         "emotion": s.emotion, "delivery": s.delivery,
         "intensity": s.intensity, "pace": s.pace,
         "pause_before_ms": s.pause_before_ms,
         "pause_after_ms": s.pause_after_ms,
         "directed": bool(s.directed), "has_audio": has_wav}
    if has_wav:
        d["audio_url"] = "/api/audio?path=" + quote(str(s.wav_path))
    return d


# ------------------------------------------------------------------
# Handler HTTP
# ------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "AureaServer/0.6"

    def log_message(self, fmt, *args):  # silencia el log por request
        pass

    # ---------- utilidades ----------
    def _send(self, code, payload=None, raw=None, content_type="application/json"):
        body = raw if raw is not None else json.dumps(
            payload if payload is not None else {},
            ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _err(self, code, msg):
        self._send(code, {"error": str(msg)})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            raise ValueError("JSON inválido en el cuerpo")

    def do_OPTIONS(self):
        self._send(204, raw=b"", content_type="text/plain")

    # ---------- GET ----------
    def do_GET(self):
        try:
            self._get()
        except FileNotFoundError as e:
            self._err(404, str(e))
        except ValueError as e:
            self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._err(500, f"{type(e).__name__}: {e}")

    def _get(self):
        path, parts, qs = self._route()

        if path == "/api/health":
            return self._send(200, {"ok": True, "version": "0.6.0-electron",
                                    "port": PORT})
        if path == "/api/voices":
            return self._send(200, {"kokoro": KOKORO_VOICES})
        if path == "/api/job":
            with _job_lock:
                return self._send(200, dict(_job, log=list(_job["log"])))
        if path == "/api/books":
            db = Database()
            return self._send(200, [_book_dict(b) for b in db.books()])
        if path == "/api/voice-profiles":
            return self._send(200, clone_store.list_profiles())

        # /api/books/{id}/chapters
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "books" \
                and parts[3] == "chapters":
            db = Database()
            book_id = int(parts[2])
            out = []
            for c in db.chapters(book_id):
                segs = db.segments(c.id)
                done = len([s for s in segs
                            if s.wav_path and Path(s.wav_path).exists()])
                cap = (Path.home() / "aurea" / "output" / f"book_{book_id}"
                       / f"cap_{c.idx:03d}.wav")
                entry = {"id": c.id, "idx": c.idx, "title": c.title,
                         "segments": len(segs), "done": done}
                if cap.exists():
                    entry["audio_url"] = "/api/audio?path=" + quote(str(cap))
                out.append(entry)
            return self._send(200, out)

        # /api/chapters/{id}
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "chapters":
            db = Database()
            ch = db.chapter(int(parts[2]))
            if not ch:
                return self._err(404, "Capítulo no encontrado")
            chars = db.characters(ch.book_id)
            cmap = {c.id: c.name for c in chars}
            segs = db.segments(ch.id)
            done = len([s for s in segs
                        if s.wav_path and Path(s.wav_path).exists()])
            cap = (Path.home() / "aurea" / "output" / f"book_{ch.book_id}"
                   / f"cap_{ch.idx:03d}.wav")
            latest_audio = ("/api/audio?path=" + quote(str(cap))
                            if cap.exists() else None)
            return self._send(200, {
                "chapter": {"id": ch.id, "book_id": ch.book_id,
                            "idx": ch.idx, "title": ch.title},
                "segments": [_seg_dict(s, cmap) for s in segs],
                "done": done,
                "total": len(segs),
                "characters": [_char_dict(c) for c in chars],
                "latest_audio": latest_audio,
            })

        # /api/books/{id}/characters
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "books" \
                and parts[3] == "characters":
            db = Database()
            return self._send(
                200, [_char_dict(c) for c in db.characters(int(parts[2]))])

        # /api/audio/peaks?path=...&n=...
        if path == "/api/audio/peaks":
            return self._serve_peaks(qs)
        # /api/audio?path=...
        if path == "/api/audio":
            return self._serve_audio(qs)

        return self._err(404, f"Ruta desconocida: {path}")

    # ---------- POST / PUT / DELETE ----------
    def do_POST(self):
        try:
            self._post()
        except ValueError as e:
            self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._err(500, f"{type(e).__name__}: {e}")

    def _post(self):
        path, parts, qs = self._route()
        try:
            body = self._body()
        except ValueError as e:
            return self._err(400, str(e))

        # Importar libro
        if path == "/api/books":
            src = str(body.get("path", "")).strip()
            if not src:
                return self._err(400, "Falta 'path' del archivo")
            p = Path(src).expanduser()
            if not p.exists():
                return self._err(404, f"No existe el archivo: {p}")
            title, chapters = import_book(str(p))
            db = Database()
            book_id = db.add_book(title, str(p), p.suffix.lower().lstrip("."))
            for idx, (ch_title, text) in enumerate(chapters):
                db.add_chapter(book_id, idx, ch_title, text)
            _log(f"Libro importado: {title} ({len(chapters)} capítulos)")
            return self._send(200, {"id": book_id, "title": title,
                                    "chapters": len(chapters)})

        # Crear segmentos del capítulo (sin TTS)
        if len(parts) == 4 and parts[1] == "chapters" and parts[3] == "segments":
            cid = int(parts[2])
            db = Database()
            ch = db.chapter(cid)
            if not ch:
                return self._err(404, "Capítulo no encontrado")
            cast = CastManager(db)
            use_cast = len(db.characters(ch.book_id)) > 1
            if use_cast:
                cast.ensure_narrator(ch.book_id)
            created = 0
            if not db.segments(cid):
                for i, (kind, speaker, chunk, pause) in enumerate(
                        segmentize(ch.text, use_cast)):
                    name = speaker if (kind == "dialogue" and speaker) else "Narrador"
                    char_id = cast.character_id(ch.book_id, name) if use_cast else None
                    db.add_segment(cid, i, chunk, kind=kind,
                                   character_id=char_id, pause_after_ms=pause)
                    created += 1
            return self._send(200, {"created": created,
                                    "total": len(db.segments(cid))})

        # Detectar reparto
        if len(parts) == 4 and parts[1] == "chapters" and parts[3] == "detect":
            cid = int(parts[2])
            db = Database()
            ch = db.chapter(cid)
            if not ch:
                return self._err(404, "Capítulo no encontrado")
            names = detect_characters([ch.text])
            cast = CastManager(db)
            created = cast.build_cast(ch.book_id, names)
            return self._send(200, {"detected": names, "created": created})

        # Saneo manual del reparto (reconciliación de voces)
        if len(parts) == 4 and parts[1] == "books" \
                and parts[3] == "cast-sanitize":
            db = Database()
            cast = CastManager(db)
            fixed = cast.sanitize_cast(int(parts[2]), set(VOICES_ES))
            _log(f"Reparto saneado: {fixed} personaje(s) reconciliados")
            return self._send(200, {"fixed": fixed})

        # Limpiar etiquetas de habla ("—dijo Narrador.") de segmentos guardados
        if len(parts) == 4 and parts[1] == "chapters" and parts[3] == "clean":
            cid = int(parts[2])
            db = Database()
            if not db.chapter(cid):
                return self._err(404, "Capítulo no encontrado")
            cleaned = 0
            for s in db.segments(cid):
                clean = clean_segment_text(s.text)
                if clean != s.text:
                    if s.wav_path and Path(s.wav_path).exists():
                        try:
                            os.remove(s.wav_path)
                        except OSError:
                            pass
                    db.conn.execute(
                        "UPDATE segments SET text=?, wav_path=NULL, "
                        "status='pending' WHERE id=?", (clean, s.id))
                    cleaned += 1
            if cleaned:
                db.conn.commit()
            _log(f"Limpieza de etiquetas: {cleaned} segmento(s) "
                 "(regenera para actualizar el audio)")
            return self._send(200, {"cleaned": cleaned})

        # Unir los segmentos ya generados en el audio del capítulo (sin TTS)
        if len(parts) == 4 and parts[1] == "chapters" and parts[3] == "assemble":
            cid = int(parts[2])
            db = Database()
            ch = db.chapter(cid)
            if not ch:
                return self._err(404, "Capítulo no encontrado")
            started = _start_job(
                "assemble", cid,
                lambda op, od, of: assemble_chapter(ch, on_progress=op,
                                                    on_done=od, on_failed=of))
            return self._accept(started)
        # Dirigir capítulo (job)
        if len(parts) == 4 and parts[1] == "chapters" and parts[3] == "direct":
            cid = int(parts[2])
            use_llm = bool(body.get("use_llm"))
            db = Database()
            if not db.chapter(cid):
                return self._err(404, "Capítulo no encontrado")
            if not db.segments(cid):
                return self._err(400,
                                 "No hay segmentos: crea los segmentos primero")
            kind = "direction" + ("+ollama" if use_llm else "")
            started = _start_job(kind, cid, lambda op, od, of: run_direction(
                cid, use_llm=use_llm,
                on_progress=op, on_done=od, on_failed=of))
            return self._accept(started)

        # Generar capítulo (job)
        if len(parts) == 4 and parts[1] == "chapters" and parts[3] == "generate":
            cid = int(parts[2])
            force = bool(body.get("force"))
            db = Database()
            ch = db.chapter(cid)
            if not ch:
                return self._err(404, "Capítulo no encontrado")
            chars = db.characters(ch.book_id)
            narrator = next((c for c in chars if c.is_narrator), None)
            voice = narrator.voice if narrator else "em_alex"
            speed = narrator.speed if narrator else 1.0

            def fn(op, od, of, ch=ch, voice=voice, speed=speed, force=force):
                run_generation(ch, voice, speed, force=force,
                               on_progress=op, on_done=od, on_failed=of)

            return self._accept(_start_job("generate", cid, fn))

        # Importar muestra de voz clonada
        if path == "/api/voice-profiles":
            name = str(body.get("name", "")).strip()
            src = str(body.get("path", "")).strip()
            if not name or not src:
                return self._err(400, "Se necesitan 'name' y 'path'")
            pid, report = import_sample(src, name)
            _log(f"Perfil de voz creado: {name} ({report['verdict']})")
            return self._send(200, {"profile_id": pid, "report": report})

        return self._err(404, f"Ruta desconocida: {path}")

    def do_PUT(self):
        try:
            path, parts, qs = self._route()
            body = self._body()
            # Actualizar personaje (voz, velocidad, motor, perfil)
            if len(parts) == 3 and parts[1] == "characters":
                char_id = int(parts[2])
                db = Database()
                voice = str(body.get("voice") or "em_alex")
                speed = float(body.get("speed") or 1.0)
                engine = str(body.get("engine") or "kokoro").lower()
                profile_id = body.get("profile_id")
                if engine == "xtts":
                    if not profile_id:
                        return self._err(400,
                                         "profile_id requerido para XTTS")
                    prof = clone_store.get_profile(int(profile_id))
                    if not prof:
                        return self._err(404, "Perfil de voz no encontrado")
                    cast = CastManager(db)
                    cast.assign(char_id, prof["name"], speed,
                                engine="xtts", profile_id=int(profile_id))
                    return self._send(200, {"ok": True})
                if voice not in KOKORO_VOICES:
                    return self._err(400,
                                     f"Voz Kokoro no válida: {voice} "
                                     f"(usa {', '.join(KOKORO_VOICES)})")
                cast = CastManager(db)
                cast.assign(char_id, voice, speed,
                            engine="kokoro", profile_id=None)
                return self._send(200, {"ok": True})
            return self._err(404, f"Ruta desconocida: {path}")
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            return self._err(500, f"{type(e).__name__}: {e}")

    def do_DELETE(self):
        try:
            path, parts, qs = self._route()
            # Borrar libro
            if len(parts) == 3 and parts[1] == "books":
                db = Database()
                db.delete_book(int(parts[2]))
                return self._send(200, {"ok": True})
            # Borrar perfil de voz clonada
            if len(parts) == 3 and parts[1] == "voice-profiles":
                clone_store.delete_profile(int(parts[2]))
                return self._send(200, {"ok": True})
            return self._err(404, f"Ruta desconocida: {path}")
        except Exception as e:  # noqa: BLE001
            return self._err(500, f"{type(e).__name__}: {e}")

    # ---------- helpers ----------
    def _accept(self, started):
        if not started:
            return self._err(409, "Ya hay un trabajo en curso")
        return self._send(200, {"ok": True, "job": "started"})

    def _route(self):
        p = urlparse(self.path)
        parts = [x for x in p.path.split("/") if x]
        return p.path, parts, parse_qs(p.query)

    def _resolve_output_wav(self, qs):
        p = Path(qs.get("path", [""])[0])
        if p.suffix.lower() != ".wav":
            return None, "Solo se sirve audio .wav"
        rp = p.resolve()
        try:
            rp.relative_to(OUTPUT_ROOT)
        except ValueError:
            return None, "Ruta fuera del directorio de salida"
        if not rp.exists():
            return None, "Archivo no encontrado"
        return rp, None

    def _serve_audio(self, qs):
        rp, err = self._resolve_output_wav(qs)
        if err:
            code = 404 if err == "Archivo no encontrado" else 403
            return self._err(code, err)
        size = rp.stat().st_size
        rng = self.headers.get("Range")
        start, length = 0, size
        if rng and rng.startswith("bytes="):
            try:
                start = int(rng.split("=", 1)[1].split("-", 1)[0] or 0)
            except ValueError:
                start = 0
            start = max(0, min(start, size - 1))
            length = size - start
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{size - 1}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with open(rp, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def _serve_peaks(self, qs):
        rp, err = self._resolve_output_wav(qs)
        if err:
            code = 404 if err == "Archivo no encontrado" else 403
            return self._err(code, err)
        try:
            n = int(qs.get("n", ["240"])[0])
        except ValueError:
            n = 240
        n = max(50, min(n, 1000))
        data, sr = sf.read(str(rp), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        step = max(1, len(mono) // n)
        peaks = [round(float(np.max(np.abs(mono[i:i + step]))), 4)
                 for i in range(0, len(mono), step)]
        return self._send(200, {"peaks": peaks,
                                "duration": round(len(mono) / sr, 2)})


def main():
    Database()  # esquema + migraciones al arrancar
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AUREA API lista en http://{HOST}:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
