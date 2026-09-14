"""Tasks do Celery: tudo que não cabe no ciclo de request do webhook.

A view devolve 200 em milissegundos e o trabalho real acontece aqui — baixar
mídia, transcrever, falar com a Claude e responder. Se isso rodasse na view, a
Meta veria respostas lentas e acabaria desabilitando a subscrição.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction

from ai.agente import Contexto, SemQuota, responder
from carteira.models import Origem

from . import janela
from .canais import obter_canal
from .conteudo import montar
from .models import Mensagem, Midia

logger = logging.getLogger(__name__)

ORIGENS = {
    Mensagem.Tipo.TEXTO: Origem.TEXTO,
    Mensagem.Tipo.AUDIO: Origem.AUDIO,
    Mensagem.Tipo.IMAGEM: Origem.IMAGEM,
    Mensagem.Tipo.DOCUMENTO: Origem.PDF,
}

CONVITE_PAREAMENTO = (
    "Oi! Eu sou a Centavo 💜\n\n"
    "Este número ainda não está ligado a nenhuma conta. Entre no portal, gere o "
    "código de pareamento e me mande ele aqui que eu conecto."
)

SEM_QUOTA = (
    "Sua cota de conversas deste mês acabou 😕 Ela reinicia no dia 1º. "
    "Enquanto isso, dá pra lançar tudo pelo portal."
)


@shared_task(bind=True, max_retries=3)
def processar_mensagem(self, mensagem_id: int, media_id: str = "") -> None:
    try:
        mensagem = Mensagem.objects.select_related("numero", "usuario").get(pk=mensagem_id)
    except Mensagem.DoesNotExist:
        logger.warning("Mensagem %s sumiu antes de ser processada.", mensagem_id)
        return

    if mensagem.status != Mensagem.Status.RECEBIDA:
        # Já processada. A task pode ser reentregue pelo broker, e reprocessar
        # criaria a mesma transação duas vezes.
        logger.info("Mensagem %s já está em %s; ignorando.", mensagem_id, mensagem.status)
        return

    Mensagem.objects.filter(pk=mensagem_id).update(status=Mensagem.Status.PROCESSANDO)
    canal = obter_canal(mensagem.canal)

    numero = mensagem.numero
    if numero is not None:
        janela.registrar_inbound(numero)

    # Número sem vínculo não tem espaço onde lançar: o caminho é o pareamento.
    usuario = mensagem.usuario or (numero.usuario if numero else None)
    if usuario is None or usuario.espaco_id is None:
        if numero is not None:
            _tentar_parear(mensagem, numero, canal)
        return

    try:
        if media_id:
            _baixar_midia(mensagem, media_id, canal)
        if mensagem.tipo == Mensagem.Tipo.AUDIO:
            _transcrever(mensagem)
    except Exception as exc:
        logger.exception("Falha ao preparar a mídia da mensagem %s", mensagem_id)
        _falhar(mensagem, numero, canal, str(exc), "Não consegui abrir esse arquivo 😕")
        return

    mensagem.refresh_from_db()

    try:
        resposta = responder(
            Contexto(
                espaco=usuario.espaco,
                usuario=usuario,
                origem=ORIGENS.get(mensagem.tipo, Origem.TEXTO),
            ),
            montar(mensagem),
            historico=_historico(mensagem),
        )
    except SemQuota:
        _responder(mensagem, numero, canal, SEM_QUOTA)
        Mensagem.objects.filter(pk=mensagem_id).update(status=Mensagem.Status.RESPONDIDA)
        return
    except Exception as exc:
        logger.exception("Agente falhou na mensagem %s", mensagem_id)
        _falhar(mensagem, numero, canal, str(exc), "Deu um problema aqui 😕 Tenta de novo?")
        return

    _responder(mensagem, numero, canal, resposta.texto or "Ok!")
    Mensagem.objects.filter(pk=mensagem_id).update(status=Mensagem.Status.RESPONDIDA)


@shared_task
def transcrever_audio(midia_id: int) -> str:
    """Fila `midia`: é a task pesada e não pode travar as mensagens de texto."""
    from ai.transcricao import transcrever

    midia = Midia.objects.select_related("mensagem").get(pk=midia_id)
    texto = transcrever(midia.arquivo.path)
    Mensagem.objects.filter(pk=midia.mensagem_id).update(transcricao=texto)
    return texto


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _baixar_midia(mensagem: Mensagem, media_id: str, canal) -> None:
    if hasattr(mensagem, "midia"):
        return

    baixada = canal.baixar_midia(media_id)
    extensao = _extensao(baixada.mime_type)
    midia = Midia(
        mensagem=mensagem,
        media_id=media_id,
        mime_type=baixada.mime_type,
        tamanho=baixada.tamanho,
    )
    midia.arquivo.save(f"{media_id}{extensao}", ContentFile(baixada.conteudo), save=False)
    midia.save()


def _transcrever(mensagem: Mensagem) -> None:
    from ai.transcricao import AudioLongoDemais, transcrever

    midia = getattr(mensagem, "midia", None)
    if midia is None or not midia.arquivo:
        return
    try:
        texto = transcrever(midia.arquivo.path)
    except AudioLongoDemais as exc:
        logger.info("Áudio recusado: %s", exc)
        texto = ""
    Mensagem.objects.filter(pk=mensagem.pk).update(transcricao=texto)


def _historico(mensagem: Mensagem) -> list[dict]:
    """Últimos turnos, para a conversa ter memória curta.

    Só texto: remandar a imagem de todo turno anterior multiplicaria o custo
    por nada — o que importa dela já virou lançamento.
    """
    from django.conf import settings

    alvo = (
        Mensagem.objects.filter(numero=mensagem.numero)
        if mensagem.numero_id
        else Mensagem.objects.filter(usuario=mensagem.usuario)
    )
    recentes = (
        alvo.exclude(pk=mensagem.pk)
        .exclude(texto="", transcricao="")
        .order_by("-criada_em")[: settings.AI_HISTORICO_TURNOS]
    )

    return [
        {
            "role": "user" if m.direcao == Mensagem.Direcao.ENTRADA else "assistant",
            "content": m.conteudo,
        }
        for m in reversed(list(recentes))
    ]


def _responder(mensagem: Mensagem, numero, canal, texto: str) -> None:
    if numero is not None:
        janela.responder(numero, texto, canal=canal)
        return
    Mensagem.objects.create(
        usuario=mensagem.usuario,
        canal=canal.nome,
        direcao=Mensagem.Direcao.SAIDA,
        tipo=Mensagem.Tipo.TEXTO,
        texto=texto,
        status=Mensagem.Status.RESPONDIDA,
    )


def _falhar(mensagem: Mensagem, numero, canal, erro: str, aviso: str) -> None:
    Mensagem.objects.filter(pk=mensagem.pk).update(status=Mensagem.Status.ERRO, erro=erro[:2000])
    _responder(mensagem, numero, canal, aviso)


@transaction.atomic
def _tentar_parear(mensagem: Mensagem, numero, canal) -> None:
    """Um número desconhecido só pode fazer uma coisa: mandar o código."""
    from django.utils import timezone

    from .models import CodigoPareamento

    codigo = (mensagem.conteudo or "").strip()
    pareamento = (
        CodigoPareamento.objects.select_related("usuario")
        .filter(codigo=codigo, usado_em__isnull=True, expira_em__gt=timezone.now())
        .first()
        if codigo.isdigit()
        else None
    )

    if pareamento is None:
        janela.responder(numero, CONVITE_PAREAMENTO, canal=canal)
        Mensagem.objects.filter(pk=mensagem.pk).update(status=Mensagem.Status.IGNORADA)
        return

    numero.usuario = pareamento.usuario
    numero.verificado_em = timezone.now()
    numero.save(update_fields=["usuario", "verificado_em"])

    pareamento.usado_em = timezone.now()
    pareamento.save(update_fields=["usado_em"])

    janela.responder(
        numero,
        "Pronto, conectei este número à sua conta ✅\n\n"
        "Agora é só me contar seus gastos: pode ser texto, áudio, print do PIX ou PDF.",
        canal=canal,
    )
    Mensagem.objects.filter(pk=mensagem.pk).update(status=Mensagem.Status.RESPONDIDA)


def _extensao(mime: str) -> str:
    return {
        "audio/ogg": ".ogg",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }.get((mime or "").split(";")[0].strip(), "")
