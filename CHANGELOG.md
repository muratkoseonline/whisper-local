# Changelog

## v1.2.0 (2026-10-06)
- GPU (CUDA) destegi: torch CUDA'li surum (`2.13.0+cu130`); `server.py` cihazi otomatik secer, GPU hatasinda CPU'ya duser. `WHISPER_DEVICE=cpu` ile CPU zorlanir.
- Arayuzde kullanilan cihaz (GPU/CPU) gorunur.
- Olcum (70 sn ses): small CPU 80 sn, GPU 29 sn; medium GPU ~58-65 sn (RTX 3060 Laptop 6 GB, tepe ~5.7 GB VRAM).

## v1.1.0 (2026-10-06)
- Ilerleme cubugu (yukleme + cozumleme), asama etiketi, gecen/kalan sure, canli metin.
- Cyberpunk arayuz, surukle-birak duzeltmesi.

Gorev: MRTKS-16119
