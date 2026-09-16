"""Transcrição de áudio com faster-whisper, local.

A API da Anthropic aceita imagem e PDF nativamente, mas **não aceita áudio** —
daí esta etapa. Roda no próprio servidor, em `int8`: o modelo `small` ocupa
~1,5 GB de RAM e transcreve a ~6x o tempo real em CPU, então um áudio de 20
segundos sai em uns 3.

Duas decisões que só aparecem em produção:

- O modelo é carregado uma vez por processo e fica em cache. Carregar por
  mensagem somaria segundos e picos de RAM a cada áudio.
- O diretório do modelo vem das settings e fica FORA da árvore do projeto: em
  BASE_DIR, cada deploy limpo rebaixaria ~500 MB.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


class AudioLongoDemais(Exception):
    """Áudio acima do teto — recusado em vez de ocupar o worker por minutos."""


@lru_cache(maxsize=1)
def obter_modelo():
    from faster_whisper import WhisperModel

    logger.info("Carregando o modelo whisper %s...", settings.WHISPER_MODELO)
    return WhisperModel(
        settings.WHISPER_MODELO,
        device=settings.WHISPER_DEVICE,
        compute_type=settings.WHISPER_COMPUTE_TYPE,
        download_root=settings.WHISPER_CACHE_DIR,
    )


def duracao_segundos(caminho: Path) -> float:
    """Duração via ffprobe. Devolve 0 quando não consegue medir — nesse caso a
    guarda de tamanho não se aplica, e o teto de tempo do próprio worker vale."""
    try:
        saida = subprocess.run(
            [
                settings.FFMPEG_BIN.replace("ffmpeg", "ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(caminho),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return float(saida.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        logger.warning("Não consegui medir a duração de %s: %s", caminho, exc)
        return 0.0


def para_wav(origem: Path) -> Path:
    """Converte o .oga/OPUS do Telegram para WAV 16 kHz mono.

    O faster-whisper lê ogg direto via PyAV, mas converter antes com ffmpeg
    torna o comportamento previsível e é o formato que o modelo espera — sem
    isso, o resample acontece dentro da biblioteca e varia com a build.
    """
    destino = Path(tempfile.mkstemp(suffix=".wav", prefix="dracma-audio-")[1])
    subprocess.run(
        [
            settings.FFMPEG_BIN,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(origem),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(destino),
        ],
        check=True,
        timeout=120,
    )
    return destino


def transcrever(caminho: str | Path) -> str:
    """Devolve o texto do áudio."""
    origem = Path(caminho)

    duracao = duracao_segundos(origem)
    if duracao > settings.WHISPER_MAX_SEGUNDOS:
        raise AudioLongoDemais(
            f"Áudio de {int(duracao)}s acima do teto de {settings.WHISPER_MAX_SEGUNDOS}s."
        )

    wav = para_wav(origem)
    try:
        segmentos, _info = obter_modelo().transcribe(
            str(wav),
            language=settings.WHISPER_IDIOMA,
            # O VAD corta silêncio e respiração, que em áudio de Telegram é
            # boa parte do arquivo: menos áudio para o modelo, mesma frase.
            vad_filter=True,
        )
        return " ".join(s.text.strip() for s in segmentos).strip()
    finally:
        wav.unlink(missing_ok=True)
