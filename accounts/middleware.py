"""Expiração da sessão de visitante.

Fica depois do MessageMiddleware no MIDDLEWARE: ao expirar, grava um aviso com
`messages`, e antes daquele ponto `request._messages` ainda não existe: a
expiração estouraria MessageFailure (500) em vez de redirecionar ao login.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone


class VisitorExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_visitante:
            if user.expirou():
                logout(request)
                messages.info(
                    request,
                    "Sua sessão de visitante expirou. Os dados temporários foram encerrados.",
                )
                return redirect(reverse("login"))
            # Renova a janela de inatividade. `update` em vez de `save()` para
            # não disparar signals nem reescrever a linha inteira a cada request.
            #
            # `get_user_model()` e não `type(user)`: request.user é um
            # SimpleLazyObject, e `type()` devolve o proxy, que não tem
            # `.objects`.
            get_user_model().objects.filter(pk=user.pk).update(ultimo_acesso=timezone.now())
        return self.get_response(request)
