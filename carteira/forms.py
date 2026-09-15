"""Formulários do portal.

Todos escrevem pelos serviços em `carteira.services`, e não direto pelo
ModelForm: é o mesmo caminho que as ferramentas da IA usam, então as regras
(normalização de valor, categoria criada sob demanda, conta que não se inventa)
valem igual nos dois lados.
"""

from __future__ import annotations

from datetime import date

from django import forms
from django.utils import timezone

from . import services
from .models import Categoria, Conta, TipoTransacao


class DataInput(forms.DateInput):
    """`<input type="date">` que de fato mostra o valor.

    O widget padrão do Django formata a data no locale (dd/mm/aaaa em pt-BR) e
    o campo nativo do HTML só aceita ISO — o resultado é um campo que aparece
    VAZIO mesmo com `initial` definido, e o formulário fica preso na validação
    do navegador sem dizer por quê.
    """

    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs={**(attrs or {}), "type": "date"}, format="%Y-%m-%d")


class ValorField(forms.CharField):
    """Dinheiro como texto, não como `type=number`.

    O campo numérico do HTML recusa a vírgula quando a página não está num
    locale pt-BR, e a pessoa daqui digita "42,50". Como `services.para_decimal`
    já entende "42,50", "1.234,56" e "R$ 19,90" — porque o mesmo valor chega
    de transcrição de áudio e de leitura de comprovante —, o proveito é usar a
    MESMA conversão nos dois caminhos, em vez de duas regras divergindo.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "decimal"}))
        super().__init__(**kwargs)

    def clean(self, value):
        texto = super().clean(value)
        if not texto and not self.required:
            return None
        try:
            return services.para_decimal(texto)
        except services.ErroDeDominio as exc:
            raise forms.ValidationError(str(exc)) from exc


class _ComEspaco(forms.Form):
    """Base dos formulários do portal: tudo é escopado a um espaço."""

    def __init__(self, *args, espaco=None, **kwargs):
        # Sem o dois-pontos do Django: os rótulos são versaletes com entreletra
        # larga, e a pontuação fica solta no fim da palavra.
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)
        self.espaco = espaco
        if espaco is None:
            return
        if "categoria" in self.fields:
            self.fields["categoria"].queryset = Categoria.objects.filter(
                espaco=espaco, ativa=True
            ).order_by("nome")
        if "conta" in self.fields:
            self.fields["conta"].queryset = Conta.objects.filter(
                espaco=espaco, ativa=True
            ).order_by("nome")


class TransacaoForm(_ComEspaco):
    tipo = forms.ChoiceField(
        label="Tipo",
        choices=[
            (TipoTransacao.DESPESA, "Saiu"),
            (TipoTransacao.RECEITA, "Entrou"),
        ],
        initial=TipoTransacao.DESPESA,
    )
    valor = ValorField(
        label="Valor", widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "0,00"})
    )
    descricao = forms.CharField(
        label="Descrição",
        max_length=140,
        widget=forms.TextInput(attrs={"placeholder": "Mercado, Uber, salário…"}),
    )
    data = forms.DateField(
        label="Data",
        widget=DataInput(),
        # Aceita o ISO que o campo nativo manda e o formato brasileiro, para o
        # caso de o navegador cair no campo de texto simples.
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        initial=timezone.localdate,
    )
    categoria = forms.ModelChoiceField(
        label="Categoria",
        queryset=Categoria.objects.none(),
        required=False,
        empty_label="Sem categoria",
    )
    conta = forms.ModelChoiceField(
        label="Conta",
        queryset=Conta.objects.none(),
        required=False,
        empty_label="Sem conta",
    )
    pago = forms.BooleanField(label="Já foi pago", required=False, initial=True)
    compartilhada = forms.ChoiceField(
        label="Quem vê",
        choices=[("1", "Todo mundo do espaço"), ("0", "Só eu")],
        initial="1",
        widget=forms.RadioSelect,
    )

    def clean_compartilhada(self):
        return self.cleaned_data["compartilhada"] == "1"

    def clean_data(self):
        valor = self.cleaned_data["data"]
        # Lançar no futuro distante quase sempre é erro de digitação no ano, e
        # a transação sumiria da visão do mês sem explicação.
        if valor > date.today().replace(year=date.today().year + 1):
            raise forms.ValidationError("Essa data está longe demais. Confira o ano.")
        return valor


class LimiteForm(_ComEspaco):
    categoria = forms.ModelChoiceField(
        label="Categoria",
        queryset=Categoria.objects.none(),
        required=False,
        empty_label="Todas as despesas (teto geral)",
    )
    valor = ValorField(
        label="Quanto posso gastar",
        widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "0,00"}),
    )
    rotulo = forms.CharField(
        label="Nome",
        max_length=60,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Presente de aniversário"}),
    )
    dias = forms.IntegerField(
        label="Dura quantos dias",
        required=False,
        min_value=1,
        max_value=365,
        widget=forms.NumberInput(attrs={"placeholder": "em branco = todo mês"}),
    )

    def clean(self):
        dados = super().clean()
        # Um limite avulso sem nome vira "geral" na listagem e fica
        # indistinguível do teto do mês.
        if dados.get("dias") and not dados.get("categoria") and not dados.get("rotulo"):
            self.add_error("rotulo", "Dê um nome ao limite avulso.")
        return dados


class RecorrenteForm(_ComEspaco):
    descricao = forms.CharField(
        label="Descrição",
        max_length=140,
        widget=forms.TextInput(attrs={"placeholder": "Aluguel, salário, assinatura…"}),
    )
    tipo = forms.ChoiceField(
        label="Tipo",
        choices=[(TipoTransacao.DESPESA, "Sai"), (TipoTransacao.RECEITA, "Entra")],
        initial=TipoTransacao.DESPESA,
    )
    valor = ValorField(
        label="Valor", widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "0,00"})
    )
    dia_do_mes = forms.IntegerField(
        label="Todo dia",
        min_value=1,
        max_value=31,
        widget=forms.NumberInput(attrs={"placeholder": "1 a 31"}),
    )
    categoria = forms.ModelChoiceField(
        label="Categoria",
        queryset=Categoria.objects.none(),
        required=False,
        empty_label="Sem categoria",
    )
    conta = forms.ModelChoiceField(
        label="Conta",
        queryset=Conta.objects.none(),
        required=False,
        empty_label="Sem conta",
    )
    compartilhada = forms.ChoiceField(
        label="Quem vê",
        choices=[("1", "Todo mundo do espaço"), ("0", "Só eu")],
        initial="1",
        widget=forms.RadioSelect,
    )

    def clean_compartilhada(self):
        return self.cleaned_data["compartilhada"] == "1"


class EntrarNoEspacoForm(forms.Form):
    """Código de convite de outro espaço."""

    codigo = forms.CharField(
        label="Código do convite",
        max_length=12,
        widget=forms.TextInput(
            attrs={"placeholder": "ABC123", "autocapitalize": "characters", "autocomplete": "off"}
        ),
    )

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


class ContaForm(_ComEspaco):
    nome = forms.CharField(
        label="Nome",
        max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "Nubank, Itaú, carteira…"}),
    )
    tipo = forms.ChoiceField(label="Tipo", choices=[])
    saldo_inicial = ValorField(
        label="Saldo de hoje",
        required=False,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "0,00"}),
    )

    def __init__(self, *args, **kwargs):
        from .models import TipoConta

        super().__init__(*args, **kwargs)
        self.fields["tipo"].choices = TipoConta.choices

    def clean_nome(self):
        nome = self.cleaned_data["nome"].strip()
        if Conta.objects.filter(espaco=self.espaco, nome__iexact=nome).exists():
            raise forms.ValidationError("Já existe uma conta com esse nome.")
        return nome
