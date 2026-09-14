"""Views de conta: modo visitante e auto-cadastro por e-mail."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.http import Http404
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_GET, require_POST

from legal.forms import AceiteForm
from legal.models import OrigemAceite
from legal.services import documentos_vigentes, registrar_aceite
from legal.utils import ip_do_request

from .forms import CadastroForm
from .limites import excedeu_limite
from .visitantes import criar_visitante


@require_POST
def entrar_visitante(request):
    """Cria um visitante temporário e autentica a sessão.

    É o que abre a demo pública: o número de teste da Meta só atende 5
    destinatários allowlistados, então sem isto ninguém de fora conseguiria
    experimentar o produto.
    """
    # O aceite é condição para criar a conta: valida ANTES de qualquer escrita,
    # para não deixar visitante órfão sem prova de aceite.
    form_aceite = AceiteForm(request.POST)
    if not form_aceite.is_valid():
        # Volta para a própria tela de aceite com o erro, e não para o login:
        # o checkbox não existe mais lá.
        return render(
            request,
            "legal/aceite.html",
            {
                "form": form_aceite,
                "documentos": list(documentos_vigentes().values()),
                "action": reverse("accounts:entrar_visitante"),
                "campos_extras": {},
            },
        )

    # Sem limite por IP, um script criaria visitantes em massa e queimaria o
    # crédito da API — cada visitante nasce com quota de IA própria.
    if excedeu_limite(f"visitante:{ip_do_request(request)}", limite=5, janela_s=3600):
        messages.error(request, "Muitos acessos de visitante deste endereço. Tente mais tarde.")
        return redirect("login")

    usuario, _senha = criar_visitante()
    login(request, usuario)
    registrar_aceite(request, usuario=usuario, origem=OrigemAceite.VISITANTE, e_visitante=True)
    messages.info(
        request,
        "Você entrou como visitante. Seus dados são temporários e expiram por inatividade.",
    )
    return redirect(settings.LEGAL_REDIRECT_URL)


# ---------------------------------------------------------------------------
# Auto-cadastro por e-mail — DESLIGADO por padrão (SIGNUP_ENABLED=False).
# Enquanto a flag for False as três views abaixo respondem 404: o recurso fica
# pronto, mas invisível e inacessível.
# ---------------------------------------------------------------------------


def _cadastro_ativo():
    if not getattr(settings, "SIGNUP_ENABLED", False):
        raise Http404()


def _enviar_confirmacao(request, usuario):
    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = default_token_generator.make_token(usuario)
    corpo = render_to_string(
        "registration/cadastro_email.html",
        {
            "user": usuario,
            "protocol": "https" if request.is_secure() else "http",
            "domain": request.get_host(),
            "uid": uid,
            "token": token,
        },
    )
    send_mail(
        subject="Confirme sua conta · Dracma",
        message=corpo,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[usuario.email],
        fail_silently=True,
    )


def cadastrar(request):
    """Cria uma conta inativa e dispara o e-mail de confirmação."""
    _cadastro_ativo()
    if request.user.is_authenticated:
        return redirect(settings.LEGAL_REDIRECT_URL)

    if request.method == "POST":
        if excedeu_limite(f"cadastro:{ip_do_request(request)}", limite=5, janela_s=3600):
            messages.error(request, "Muitas tentativas deste endereço. Tente mais tarde.")
            return redirect("login")
        form = CadastroForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            _enviar_confirmacao(request, usuario)
            return redirect("accounts:cadastro_enviado")
    else:
        form = CadastroForm()
    return render(request, "registration/cadastro.html", {"form": form})


def cadastro_enviado(request):
    _cadastro_ativo()
    return render(request, "registration/cadastro_enviado.html")


@require_GET
def confirmar_email(request, uidb64, token):
    """Valida o link e ativa a conta, autenticando o usuário."""
    _cadastro_ativo()
    Usuario = get_user_model()
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        usuario = Usuario.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, Usuario.DoesNotExist):
        usuario = None

    if (
        usuario is not None
        and not usuario.is_active
        and default_token_generator.check_token(usuario, token)
    ):
        usuario.is_active = True
        usuario.save(update_fields=["is_active"])
        _preparar_espaco(usuario)
        login(request, usuario)
        # O aceite foi dado no formulário de cadastro; registra-se aqui, na
        # confirmação, que é quando a conta passa a existir de fato.
        registrar_aceite(request, usuario=usuario, origem=OrigemAceite.CADASTRO)
        messages.success(request, "Conta confirmada! Boas-vindas à Dracma.")
        return redirect(settings.LEGAL_REDIRECT_URL)

    messages.error(request, "Link de confirmação inválido ou expirado.")
    return redirect("login")


def _preparar_espaco(usuario):
    """Toda conta precisa de um espaço com categorias para a primeira mensagem
    não cair em 'sem categoria'."""
    from carteira.seeds import semear_categorias

    from .models import Espaco

    if usuario.espaco_id is None:
        usuario.espaco = Espaco.objects.create(nome="Meu espaço")
        usuario.save(update_fields=["espaco"])
    semear_categorias(usuario.espaco)
