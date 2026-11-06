"""Ponta a ponta no navegador: o fluxo principal do portal.

Fica sob o marcador `e2e` (fora da suíte padrão pelo pytest.ini); o job `e2e`
do CI instala o Chromium e roda `-m e2e`. Usa a fixture `page` do
pytest-playwright, que só o job `e2e` instala: por isso o Playwright não é
importado no topo do módulo, senão a coleta da suíte comum quebra. Na mão:

    pip install pytest-playwright && playwright install chromium
    pytest -m e2e carteira/tests_e2e.py

O que só o navegador prova: o diálogo de lançamento chega por HTMX, abre como
<dialog> modal, o POST volta pelo mesmo canal e o painel inteiro se refaz com
o saldo novo. Teste de unidade vê cada view isolada, não essa costura.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from accounts.models import Espaco, Usuario
from carteira.models import Categoria, Conta, Transacao
from carteira.seeds import semear_categorias
from legal.models import DocumentoLegal

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def expect(alvo):
    """`playwright.sync_api.expect`, importado só quando o teste roda."""
    from playwright.sync_api import expect as _expect

    return _expect(alvo)


SENHA = "senha-e2e-bem-comprida-123"


@pytest.fixture
def usuario(db):
    espaco = Espaco.objects.create(nome="Casa")
    semear_categorias(espaco)
    Conta.objects.create(espaco=espaco, nome="Nubank", saldo_inicial=Decimal("1000"))
    usuario = Usuario.objects.create_user(
        username="ana", email="ana@exemplo.com", password=SENHA, espaco=espaco
    )
    # Sem documento legal publicado o middleware de aceite não intercepta, e
    # é esse o caso de um usuário que já aceitou: o teste mira o portal.
    assert not DocumentoLegal.objects.exists()
    return usuario


@pytest.fixture
def erros_js(page: Page):
    erros: list[str] = []
    page.on("pageerror", lambda e: erros.append(str(e)))
    page.on(
        "console",
        lambda m: erros.append(m.text) if m.type == "error" else None,
    )
    return erros


def entrar(page: Page, base: str, usuario: Usuario) -> None:
    page.goto(f"{base}/login/")
    page.fill("#id_username", usuario.username)
    page.fill("#id_password", SENHA)
    page.click("button[type=submit]")


def test_login_leva_ao_painel(live_server, page: Page, usuario, erros_js):
    entrar(page, live_server.url, usuario)
    expect(page).to_have_url(re.compile(rf"^{re.escape(live_server.url)}/$"))
    expect(page.get_by_role("button", name="Novo lançamento")).to_be_visible()
    assert erros_js == [], erros_js


def test_novo_lancamento_pelo_dialogo_atualiza_o_painel(live_server, page: Page, usuario, erros_js):
    entrar(page, live_server.url, usuario)

    page.get_by_role("button", name="Novo lançamento").click()
    dialogo = page.locator("dialog#dialogo")
    expect(dialogo).to_be_visible()
    # Modal de verdade: aberto por showModal(), não só exibido.
    assert dialogo.evaluate("d => d.open && d.matches(':modal')")

    dialogo.locator("#id_tipo").select_option("despesa")
    dialogo.locator("#id_valor").fill("247,80")
    dialogo.locator("#id_descricao").fill("Mercado do mês")
    # O rótulo da opção leva o emoji da categoria; o valor (pk) é estável.
    mercado = Categoria.objects.get(espaco=usuario.espaco, nome="Mercado")
    dialogo.locator("#id_categoria").select_option(str(mercado.pk))
    dialogo.locator("button[type=submit]").click()

    # O POST volta por HTMX: o diálogo fecha e o painel mostra o lançamento.
    expect(dialogo).to_be_hidden()
    expect(page.get_by_text("Mercado do mês").first).to_be_visible()

    transacao = Transacao.objects.get(descricao="Mercado do mês")
    assert transacao.valor == Decimal("247.80")
    assert transacao.espaco_id == usuario.espaco_id
    assert transacao.categoria_id == mercado.pk
    assert erros_js == [], erros_js


def test_dialogo_fecha_com_esc_sem_gravar(live_server, page: Page, usuario, erros_js):
    entrar(page, live_server.url, usuario)
    page.get_by_role("button", name="Novo lançamento").click()
    dialogo = page.locator("dialog#dialogo")
    expect(dialogo).to_be_visible()
    dialogo.locator("#id_descricao").fill("Não era para gravar")

    page.keyboard.press("Escape")

    expect(dialogo).to_be_hidden()
    assert not Transacao.objects.filter(descricao="Não era para gravar").exists()
    assert erros_js == [], erros_js
