"""Tasks do Celery: tudo que não cabe no ciclo de request do webhook.

A view devolve 200 em milissegundos e o trabalho real acontece aqui — baixar
mídia, transcrever, falar com a Claude e responder. Se isso rodasse na view, o
Telegram veria respostas lentas e passaria a espaçar as entregas.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction

from ai.agente import Contexto, SemQuota, responder
from carteira.models import Origem

from . import envio, onboarding
from .canais import obter_canal
from .conteudo import montar
from .models import Mensagem, Midia
from .webhook import comando_start

logger = logging.getLogger(__name__)

ORIGENS = {
    Mensagem.Tipo.TEXTO: Origem.TEXTO,
    Mensagem.Tipo.AUDIO: Origem.AUDIO,
    Mensagem.Tipo.IMAGEM: Origem.IMAGEM,
    Mensagem.Tipo.DOCUMENTO: Origem.PDF,
}

SEM_QUOTA = (
    "Sua cota de conversas deste mês acabou 😕 Ela reinicia no dia 1º. "
    "Enquanto isso, dá pra lançar tudo pelo portal."
)

JA_CONECTADO = "Você já está conectado 💜 Pode mandar seus gastos que eu registro."


@shared_task(bind=True, max_retries=3)
def processar_mensagem(self, mensagem_id: int, file_id: str = "", mime_hint: str = "") -> None:
    try:
        mensagem = Mensagem.objects.select_related("conta", "usuario").get(pk=mensagem_id)
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

    conta = mensagem.conta

    # Conversa sem vínculo não tem espaço onde lançar: o caminho é o pareamento.
    usuario = mensagem.usuario or (conta.usuario if conta else None)
    if usuario is None or usuario.espaco_id is None:
        if conta is not None:
            _tentar_parear(mensagem, conta, canal)
        return

    # `/start` de quem já está conectado não vai para o agente: ele o leria
    # como uma fala qualquer e tentaria achar um gasto em "/start".
    if conta is not None and comando_start(mensagem.texto) is not None:
        envio.responder(conta, JA_CONECTADO, canal=canal)
        Mensagem.objects.filter(pk=mensagem_id).update(status=Mensagem.Status.RESPONDIDA)
        return

    try:
        if file_id:
            _baixar_midia(mensagem, file_id, mime_hint, canal)
        if mensagem.tipo == Mensagem.Tipo.AUDIO:
            _transcrever(mensagem)
    except Exception as exc:
        logger.exception("Falha ao preparar a mídia da mensagem %s", mensagem_id)
        _falhar(mensagem, conta, canal, str(exc), "Não consegui abrir esse arquivo 😕")
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
        _responder(mensagem, conta, canal, SEM_QUOTA)
        Mensagem.objects.filter(pk=mensagem_id).update(status=Mensagem.Status.RESPONDIDA)
        return
    except Exception as exc:
        logger.exception("Agente falhou na mensagem %s", mensagem_id)
        _falhar(mensagem, conta, canal, str(exc), "Deu um problema aqui 😕 Tenta de novo?")
        return

    _responder(mensagem, conta, canal, resposta.texto or "Ok!", turno=resposta.turno)
    Mensagem.objects.filter(pk=mensagem_id).update(status=Mensagem.Status.RESPONDIDA)

    # O roteiro avança DEPOIS da resposta, e só quando o agente de fato fez
    # algo: uma dica emendada numa conversa que falhou é ruído.
    if conta is not None and resposta.ferramentas_usadas:
        onboarding.avancar(conta, canal=canal)


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


def _baixar_midia(mensagem: Mensagem, file_id: str, mime_hint: str, canal) -> None:
    if hasattr(mensagem, "midia"):
        return

    baixada = canal.baixar_midia(file_id, mime_hint)
    extensao = _extensao(baixada.mime_type)
    midia = Midia(
        mensagem=mensagem,
        file_id=file_id,
        mime_type=baixada.mime_type,
        tamanho=baixada.tamanho,
    )
    # O file_id do Telegram é longo e cheio de `-` e `_`; cortamos para o nome
    # do arquivo não estourar o limite do sistema de arquivos.
    midia.arquivo.save(f"{file_id[:60]}{extensao}", ContentFile(baixada.conteudo), save=False)
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

    Reproduz os blocos `tool_use`/`tool_result` guardados em `Mensagem.turno`,
    e não só o texto. A diferença não é cosmética: com o histórico só de
    texto, o modelo lê a própria confirmação (“Uber de R$ 20 registrado ✅”)
    como narração e refaz a tool — em produção isso duplicou um lançamento e
    ainda confirmou um ajuste que não tinha feito.

    O que não tem turno guardado (onboarding, respostas anteriores a este
    campo, erros) entra como texto, que é o melhor disponível.
    """
    from django.conf import settings

    alvo = (
        Mensagem.objects.filter(conta=mensagem.conta)
        if mensagem.conta_id
        else Mensagem.objects.filter(usuario=mensagem.usuario)
    )
    recentes = (
        alvo.filter(criada_em__lt=mensagem.criada_em)
        .exclude(pk=mensagem.pk)
        .order_by("-criada_em")[: settings.AI_HISTORICO_TURNOS]
    )

    historico: list[dict] = []
    for m in reversed(list(recentes)):
        if m.direcao == Mensagem.Direcao.ENTRADA:
            # Texto e não mídia: remandar a imagem de todo turno anterior
            # multiplicaria o custo por nada — o que importava dela já virou
            # lançamento.
            if m.conteudo:
                historico.append({"role": "user", "content": m.conteudo})
        elif m.turno:
            historico.extend(m.turno)
        elif m.conteudo:
            historico.append({"role": "assistant", "content": m.conteudo})

    return _comecando_no_usuario(historico)


def _comecando_no_usuario(historico: list[dict]) -> list[dict]:
    """A API exige que a conversa comece por uma fala do usuário.

    A janela pode cair no meio de um turno — ou logo depois de uma mensagem do
    roteiro de onboarding, que não responde a ninguém.
    """
    while historico and historico[0]["role"] != "user":
        historico.pop(0)
    return historico


def _responder(mensagem: Mensagem, conta, canal, texto: str, turno=None) -> None:
    if conta is not None:
        envio.responder(conta, texto, canal=canal, turno=turno)
        return
    Mensagem.objects.create(
        usuario=mensagem.usuario,
        canal=canal.nome,
        direcao=Mensagem.Direcao.SAIDA,
        tipo=Mensagem.Tipo.TEXTO,
        texto=texto,
        status=Mensagem.Status.RESPONDIDA,
        turno=turno or None,
    )


def _falhar(mensagem: Mensagem, conta, canal, erro: str, aviso: str) -> None:
    Mensagem.objects.filter(pk=mensagem.pk).update(status=Mensagem.Status.ERRO, erro=erro[:2000])
    _responder(mensagem, conta, canal, aviso)


@transaction.atomic
def _tentar_parear(mensagem: Mensagem, conta, canal) -> None:
    """Uma conversa desconhecida só pode fazer uma coisa: apresentar a credencial.

    Duas formas chegam aqui. O deep link manda `/start <token>` sozinho, e é o
    caminho de um toque. Quem abriu o bot pela busca digita o código de 6
    dígitos. Os dois consomem o mesmo `CodigoPareamento`.
    """
    from django.utils import timezone

    from .models import CodigoPareamento

    texto = (mensagem.conteudo or "").strip()
    payload = comando_start(texto)

    consulta = CodigoPareamento.objects.select_related("usuario").filter(
        usado_em__isnull=True, expira_em__gt=timezone.now()
    )
    if payload:
        pareamento = consulta.filter(token=payload).first()
    elif texto.isdigit():
        pareamento = consulta.filter(codigo=texto).first()
    else:
        pareamento = None

    if pareamento is None:
        envio.responder(conta, onboarding.texto_convite(), canal=canal)
        Mensagem.objects.filter(pk=mensagem.pk).update(status=Mensagem.Status.IGNORADA)
        return

    conta.usuario = pareamento.usuario
    conta.verificado_em = timezone.now()
    # Quem acabou de parear obviamente não está bloqueando o bot; uma marca
    # velha de um vínculo anterior faria os alertas nascerem desligados.
    conta.bloqueado_em = None
    conta.save(update_fields=["usuario", "verificado_em", "bloqueado_em"])

    pareamento.usado_em = timezone.now()
    pareamento.save(update_fields=["usado_em"])

    # Boas-vindas são a primeira etapa do roteiro, não uma linha solta: sem
    # elas a pessoa fica olhando para uma conversa vazia sem saber que pode
    # mandar áudio, foto de comprovante ou pedir um limite.
    onboarding.avancar(conta, canal=canal)
    Mensagem.objects.filter(pk=mensagem.pk).update(status=Mensagem.Status.RESPONDIDA)


def _extensao(mime: str) -> str:
    return {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }.get((mime or "").split(";")[0].strip(), "")
