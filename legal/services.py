"""Regras de aceite: o que está vigente, quem já aceitou, como registrar."""

from django.db import transaction

from .models import AceiteLegal, DocumentoLegal, StatusDocumento, TipoDocumento
from .utils import ip_do_request

# Chave usada na sessão para lembrar o aceite de quem não tem conta.
SESSAO_ACEITE = "legal_aceite_versoes"


def documentos_vigentes():
    """{tipo: DocumentoLegal} com a versão publicada de cada documento.

    Sem cache de propósito: são duas linhas por request, e um cache mal invalidado
    aqui significaria alguém aceitar uma versão que já não é a vigente.
    """
    vigentes = {}
    for tipo in TipoDocumento.values:
        documento = DocumentoLegal.objects.vigente(tipo)
        if documento is not None:
            vigentes[tipo] = documento
    return vigentes


def documentos_pendentes(user):
    """Documentos vigentes e materiais que este usuário ainda não aceitou."""
    vigentes = documentos_vigentes()
    if not vigentes:
        return []

    materiais = [doc for doc in vigentes.values() if doc.material]
    if not materiais:
        return []

    if user is None or not user.is_authenticated:
        return materiais

    ja_aceitos = set(
        AceiteLegal.objects.filter(usuario=user, documento__in=materiais).values_list(
            "documento_id", flat=True
        )
    )
    return [doc for doc in materiais if doc.pk not in ja_aceitos]


def precisa_reaceitar(user):
    return bool(documentos_pendentes(user))


def aceite_anonimo_valido(request):
    """A sessão atual já aceitou todas as versões vigentes?"""
    vigentes = documentos_vigentes()
    if not vigentes:
        return True
    registrado = set(request.session.get(SESSAO_ACEITE) or [])
    return all(doc.pk in registrado for doc in vigentes.values())


def _montar_evidencia(request, vigentes):
    return {
        "host": request.get_host(),
        "path": request.get_full_path(),
        "metodo": request.method,
        "referer": request.META.get("HTTP_REFERER", ""),
        "x_forwarded_for": request.META.get("HTTP_X_FORWARDED_FOR", ""),
        "accept_language": request.META.get("HTTP_ACCEPT_LANGUAGE", ""),
        "seguro": request.is_secure(),
        "versoes_vigentes": {
            tipo: {"versao": doc.versao, "sha256": doc.sha256} for tipo, doc in vigentes.items()
        },
    }


def _rotulo(usuario):
    if usuario is None:
        return ""
    for campo in ("email", "username"):
        valor = getattr(usuario, campo, "")
        if valor:
            return str(valor)[:200]
    return str(usuario)[:200]


@transaction.atomic
def registrar_aceite(request, *, usuario=None, origem, documentos=None, e_visitante=False):
    """Grava um AceiteLegal por documento vigente e devolve os registros criados.

    Chame **depois** de validar o checkbox: esta função não julga se houve aceite,
    ela apenas o documenta.
    """
    vigentes = documentos_vigentes()
    alvos = documentos if documentos is not None else list(vigentes.values())
    if not alvos:
        return []

    if usuario is None and getattr(request, "user", None) is not None:
        if request.user.is_authenticated:
            usuario = request.user

    if not request.session.session_key:
        request.session.save()

    evidencia = _montar_evidencia(request, vigentes)
    agente = request.META.get("HTTP_USER_AGENT", "")[:2000]
    ip = ip_do_request(request) or None

    registros = [
        AceiteLegal(
            documento=documento,
            usuario=usuario,
            usuario_label=_rotulo(usuario),
            e_visitante=e_visitante,
            session_key=request.session.session_key or "",
            ip=ip,
            user_agent=agente,
            origem=origem,
            documento_sha256=documento.sha256,
            evidencia=evidencia,
        )
        for documento in alvos
    ]
    AceiteLegal.objects.bulk_create(registros)

    # Anônimos não têm a quem vincular o aceite: fica na sessão, que é o mesmo
    # escopo dos dados dele.
    if usuario is None:
        aceitos = set(request.session.get(SESSAO_ACEITE) or [])
        aceitos.update(documento.pk for documento in alvos)
        request.session[SESSAO_ACEITE] = sorted(aceitos)

    return registros


def documento_publicado(tipo):
    """Versão vigente de um tipo, ou None."""
    return DocumentoLegal.objects.vigente(tipo)


def historico(tipo):
    """Versões publicadas e arquivadas, da mais recente para a mais antiga."""
    return DocumentoLegal.objects.filter(
        tipo=tipo,
        status__in=[StatusDocumento.PUBLICADO, StatusDocumento.ARQUIVADO],
    ).order_by("-publicado_em")
