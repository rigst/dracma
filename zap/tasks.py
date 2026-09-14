"""Tasks do Celery — processamento fora do ciclo de request."""

from celery import shared_task


@shared_task
def processar_mensagem(mensagem_id: int, media_id: str = "") -> None:
    raise NotImplementedError("Próximo passo: agente e transcrição.")
