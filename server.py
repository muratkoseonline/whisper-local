from __future__ import annotations

import cgi
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
WHISPER = ROOT / ".venv" / "Scripts" / "whisper.exe"
HOST = "127.0.0.1"
PORT = 8765
MAX_UPLOAD = 200 * 1024 * 1024
ALLOWED = {".ogg", ".opus", ".m4a", ".mp3", ".wav", ".mp4", ".webm", ".flac"}
TIMEOUT = 3600
RESULT_TTL = 600
ETA_MIN_PERCENT = 5.0

SEGMENT_RE = re.compile(
    r"^\[(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\s*-->\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$"
)

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
_DEVICE: str | None = None


def pick_device() -> str:
    """'cuda' when torch sees a CUDA GPU, else 'cpu'. WHISPER_DEVICE=cpu forces CPU."""
    global _DEVICE
    if _DEVICE is None:
        _DEVICE = "cpu"
        if os.environ.get("WHISPER_DEVICE", "").lower() != "cpu":
            try:
                import torch
                if torch.cuda.is_available():
                    _DEVICE = "cuda"
            except Exception:
                pass
    return _DEVICE


def _seconds(h: str | None, m: str, s: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + float(s)


def probe_duration(path: Path) -> float | None:
    """Total audio length in seconds via ffprobe; None if unreadable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        value = float(out)
        return value if value > 0 else None
    except Exception:
        return None


def purge_old_jobs() -> None:
    now = time.time()
    with LOCK:
        for jid in [j for j, v in JOBS.items() if v.get("finished") and now - v["finished"] > RESULT_TTL]:
            del JOBS[jid]


def busy() -> bool:
    with LOCK:
        return any(not v.get("finished") for v in JOBS.values())


def run_job(job_id: str, source: Path, temp_dir: Path, model: str, language: str) -> None:
    job = JOBS[job_id]

    def fail(message: str) -> None:
        with LOCK:
            job.update(stage="error", error=message, finished=time.time())

    try:
        duration = probe_duration(source)
        with LOCK:
            job.update(duration=duration, stage="loading")

        output_dir = temp_dir / "out"
        output_dir.mkdir()
        devices = [pick_device()]
        if devices[0] == "cuda":
            devices.append("cpu")  # GPU hatasinda (ornegin bellek) CPU'ya dus
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        tail: list[str] = []
        segments: list[str] = []
        returncode = 1
        for device in devices:
            cmd = [str(WHISPER), str(source), "--model", model, "--task", "transcribe", "--output_format", "txt",
                   "--output_dir", str(output_dir), "--device", device]
            if device == "cpu":
                cmd += ["--fp16", "False"]
            if language != "auto":
                cmd += ["--language", language]
            with LOCK:
                job.update(device=device, stage="loading", percent=None)
                job.pop("first_segment", None)
            tail, segments = [], []
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", cwd=str(ROOT), env=env)
            timer = threading.Timer(TIMEOUT, proc.kill)
            timer.start()
            try:
                for raw in proc.stdout:
                    line = raw.rstrip("\r\n")
                    match = SEGMENT_RE.match(line)
                    if not match:
                        if line.strip():
                            tail = (tail + [line])[-12:]
                        continue
                    end = _seconds(match.group(4), match.group(5), match.group(6))
                    segments.append(match.group(7).strip())
                    with LOCK:
                        if job["stage"] == "loading":
                            job.update(stage="transcribing", first_segment=time.time())
                        if duration:
                            job["percent"] = round(min(end / duration * 100, 99.0), 1)
                        job["text_so_far"] = "\n".join(segments)
                proc.wait()
            finally:
                timer.cancel()
            returncode = proc.returncode
            if returncode == 0:
                break

        if returncode != 0:
            return fail(("\n".join(tail) or "Whisper işlemi başarısız.")[-1200:])
        transcript_file = output_dir / "input.txt"
        if not transcript_file.exists():
            return fail(("Whisper çıktı dosyası oluşturmadı. " + " ".join(tail[-3:])).strip()[-600:])
        text = transcript_file.read_text(encoding="utf-8", errors="replace")
        with LOCK:
            job.update(stage="done", percent=100.0, text=text, text_so_far=text, finished=time.time())
    except Exception as exc:
        fail(html.escape(str(exc)))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "WhisperLocal/1.2"

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            return
        if path.startswith("/progress/"):
            return self._progress(path.rsplit("/", 1)[-1])
        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def _progress(self, job_id: str) -> None:
        purge_old_jobs()
        with LOCK:
            job = JOBS.get(job_id)
            if job is None:
                return self._json(404, {"error": "İş bulunamadı (süresi dolmuş olabilir)."})
            now = time.time()
            elapsed = (job.get("finished") or now) - job["started"]
            percent = job.get("percent")
            eta = None
            first = job.get("first_segment")
            if job["stage"] == "transcribing" and first and percent and percent >= ETA_MIN_PERCENT:
                spent = now - first
                eta = max(0, round(spent * (100 - percent) / percent))
            payload = {
                "stage": job["stage"],
                "percent": percent,
                "elapsed": round(elapsed),
                "eta": eta,
                "text_so_far": job.get("text_so_far", ""),
                "error": job.get("error"),
                "filename": job["filename"],
                "model": job["model"],
                "device": job.get("device"),
            }
            if job["stage"] == "done":
                payload["text"] = job["text"]
        self._json(200, payload)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/transcribe":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD:
            return self._json(413, {"error": "Dosya boş veya 200 MB sınırını aşıyor."})
        if not self.headers.get_content_type().startswith("multipart/form-data"):
            return self._json(400, {"error": "multipart/form-data bekleniyor."})
        if busy():
            return self._json(409, {"error": "Başka bir dönüştürme sürüyor. Bitmesini bekleyin."})

        temp_dir = Path(tempfile.mkdtemp(prefix="whisper-local-"))
        started = False
        try:
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers["Content-Type"]},
            )
            upload = form["audio"] if "audio" in form else None
            if upload is None or not getattr(upload, "filename", None):
                return self._json(400, {"error": "Bir ses dosyası seçin."})

            suffix = Path(upload.filename).suffix.lower()
            if suffix not in ALLOWED:
                return self._json(415, {"error": "Desteklenmeyen dosya türü."})
            source = temp_dir / f"input{suffix}"
            with source.open("wb") as target:
                shutil.copyfileobj(upload.file, target)
            if source.stat().st_size > MAX_UPLOAD:
                return self._json(413, {"error": "Dosya 200 MB sınırını aşıyor."})

            language = str(form.getfirst("language", "tr")).lower()
            if language not in {"tr", "en", "de", "fr", "es", "it", "auto"}:
                language = "tr"
            model = str(form.getfirst("model", "small"))
            if model not in {"tiny", "base", "small", "medium"}:
                model = "small"

            job_id = uuid.uuid4().hex[:12]
            with LOCK:
                if any(not v.get("finished") for v in JOBS.values()):
                    return self._json(409, {"error": "Başka bir dönüştürme sürüyor. Bitmesini bekleyin."})
                JOBS[job_id] = {"stage": "preparing", "percent": None, "started": time.time(),
                                "filename": Path(upload.filename).name, "model": model, "text_so_far": ""}
            threading.Thread(target=run_job, args=(job_id, source, temp_dir, model, language), daemon=True).start()
            started = True
            return self._json(202, {"job_id": job_id})
        except Exception as exc:
            return self._json(500, {"error": html.escape(str(exc))})
        finally:
            if not started:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _json(self, status: int, value: dict) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


if __name__ == "__main__":
    if not WHISPER.exists():
        raise SystemExit(f"Whisper bulunamadı: {WHISPER}")
    print(f"Whisper Local http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
