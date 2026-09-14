"""Formulários de conta."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from legal.forms import AceiteLegalMixin

User = get_user_model()


class CadastroForm(AceiteLegalMixin, UserCreationForm):
    """Cadastro público: usuário + e-mail (obrigatório e único) + senha."""

    email = forms.EmailField(
        required=True,
        label="E-mail",
        help_text="Usado para confirmar a conta e recuperar a senha.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Já existe uma conta com este e-mail.")
        return email

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.email = self.cleaned_data["email"]
        # Nasce inativa: só é ativada depois de confirmar o e-mail.
        usuario.is_active = False
        if commit:
            usuario.save()
        return usuario
