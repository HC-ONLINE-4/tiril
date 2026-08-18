#!/usr/bin/env python3
"""
Transcribe local de videos (Whisper offline, sin nube).
Extrae TODO lo que se dice (ignora musica/sonidos de fondo con VAD).

Uso:
  python transcribe.py video.mp4 [video2.mp4 ...]
  python transcribe.py --folder grabaciones
  python transcribe.py --folder grabaciones --model medium

Opciones:
  --model small|base|medium|large-v3   modelo (defecto: small; medium si hay
                                       mucha musica de fondo)
  --lang es                            idioma (defecto: auto-detecta)
  --folder DIR                         transcribe todos los .mp4 de la carpeta

Salida: un .txt (y .srt) al lado de cada video con la transcripcion completa.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from faster_whisper import WhisperModel

OUTPUT_DIR = Path(__file__).parent / "transcripciones"


def extract_audio(video: Path) -> Path:
    tmp = Path(tempfile.gettempdir()) / f"{video.stem}_audio.wav"
    cmd = ["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(tmp)]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return tmp


def format_ts(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def transcribe(video: Path, model, lang: str):
    print(f"== {video.name} ==")
    audio = extract_audio(video)

    segments, info = model.transcribe(
        str(audio),
        language=lang or None,
        vad_filter=True,          # descarta musica y silencios: solo voz
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
        condition_on_previous_text=True,
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    txt_path = OUTPUT_DIR / f"{video.stem}.txt"
    srt_path = OUTPUT_DIR / f"{video.stem}.srt"

    lines, srt_blocks = [], []
    srt_i = 1
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        ts = format_ts(seg.start)
        lines.append(f"[{ts}] {text}")
        start = seg.start
        end = seg.end
        srt_blocks.append(
            f"{srt_i}\n{format_ts(start)} --> {format_ts(end)}\n{text}\n"
        )
        srt_i += 1
        print(f"  [{ts}] {text}")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    srt_path.write_text("\n".join(srt_blocks), encoding="utf-8")
    audio.unlink(missing_ok=True)

    total = sum(seg.end - seg.start for seg in segments if seg.text.strip())
    print(f"  -> {txt_path.name} ({len(lines)} lineas, ~{int(total // 60)} min de voz)")
    print()


def main():
    p = argparse.ArgumentParser(description="Transcribe videos localmente con Whisper")
    p.add_argument("videos", nargs="*", help="Archivos de video a transcribir")
    p.add_argument("--folder", help="Carpeta con videos (transcribe todos los .mp4)")
    p.add_argument("--model", default="small",
                   choices=["tiny", "base", "small", "medium", "large-v3"],
                   help="Modelo Whisper (defecto: small)")
    p.add_argument("--lang", default="", help="Idioma, ej: es (defecto: auto)")
    args = p.parse_args()

    files = [Path(v) for v in args.videos]
    if args.folder:
        files += sorted(Path(args.folder).glob("*.mp4"))
    files = [f for f in files if f.exists()]
    if not files:
        print("No hay videos. Uso: python transcribe.py video.mp4 [--folder grabaciones]")
        sys.exit(1)

    print(f"Cargando modelo '{args.model}' (primera vez se descarga, ~{300 if args.model in ('small','base') else 1200} MB)...")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    for f in files:
        try:
            transcribe(f, model, args.lang)
        except Exception as e:
            print(f"Error con {f.name}: {e}")

    print("Listo. Transcripciones en:", OUTPUT_DIR)


if __name__ == "__main__":
    main()