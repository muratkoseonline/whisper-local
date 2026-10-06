# Whisper Local

Yerel Whisper ile ses kaydini metne ceviren kucuk arac. Dosya bilgisayardan cikmaz.

- Sunucu: `server.py` (Python stdlib `http.server`), arayuz: `index.html` (tek dosya)
- Calistirma: `start-whisper.cmd` -> http://127.0.0.1:8765
- Gereken: `.venv` icinde `openai-whisper` (`.venv/Scripts/whisper.exe`) ve PATH'te `ffmpeg`/`ffprobe`
- GPU: `.venv` icinde CUDA'li torch (`pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cu130 --force-reinstall --no-deps`). CUDA varsa otomatik kullanilir, yoksa CPU. `WHISPER_DEVICE=cpu` ile CPU zorlanir.
- Ozellikler: yukleme ve cozumleme ilerleme cubugu, canli metin, surukle-birak, model ve dil secimi, 200 MB siniri

ClickUp: MRTKS-16119
