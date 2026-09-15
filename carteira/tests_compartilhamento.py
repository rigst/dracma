"""Pessoal × compartilhado, e a mudança de espaço.

É a área em que um erro não dá tela quebrada: dá vazamento. Por isso a regra
mora em `services.visiveis_para` e estes testes batem em cada caminho que
consulta dinheiro.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import espacos
from accounts.models import ConviteEspaco, Espaco, Usuario
from carteira import services
from carteira.models import Conta, Limite, TipoTransacao, Transacao
from carteira.seeds import semear_categorias


class BaseCasalTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.conta = Conta.objects.create(
            espaco=self.espaco, nome="Conjunta", saldo_inicial=Decimal("1000")
        )
        self.ana = Usuario.objects.create_user(
            username="ana",
            email="ana@exemplo.com",
            password="x",
            first_name="Ana",
            espaco=self.espaco,
        )
        self.bia = Usuario.objects.create_user(
            username="bia",
            email="bia@exemplo.com",
            password="x",
            first_name="Bia",
            espaco=self.espaco,
        )

    def _lancar(self, autor, valor, descricao, compartilhada, **extra):
        return services.registrar_transacao(
            espaco=self.espaco,
            valor=valor,
            descricao=descricao,
            autor=autor,
            compartilhada=compartilhada,
            **extra,
        )


class VisibilidadeTest(BaseCasalTest):
    def setUp(self):
        super().setUp()
        self._lancar(self.ana, "300", "Mercado da casa", True, categoria="Mercado")
        self._lancar(self.ana, "120", "Presente surpresa", False, categoria="Presentes")
        self._lancar(self.bia, "80", "Livro da Bia", False, categoria="Educação")

    def _resumo(self, usuario):
        inicio, fim = services.limites_do_mes()
        return services.resumo_periodo(self.espaco, inicio, fim, usuario=usuario)

    def test_cada_um_ve_a_propria_fatia_mais_o_proprio(self):
        # O mercado da casa (R$ 300) é dividido: R$ 150 para cada.
        self.assertEqual(self._resumo(self.ana).despesas, Decimal("270.00"))
        self.assertEqual(self._resumo(self.bia).despesas, Decimal("230.00"))

    def test_sem_recorte_ve_tudo(self):
        # `usuario=None` é o que as rotinas de manutenção usam.
        self.assertEqual(self._resumo(None).despesas, Decimal("500.00"))

    def test_o_pessoal_de_um_nao_aparece_para_o_outro(self):
        codigos = {
            t.codigo
            for t in services.visiveis_para(Transacao.objects.filter(espaco=self.espaco), self.bia)
        }
        pessoal_da_ana = Transacao.objects.get(descricao="Presente surpresa")
        self.assertNotIn(pessoal_da_ana.codigo, codigos)

    def test_saldo_da_conta_tambem_e_recortado(self):
        # Numa conta conjunta, ver a diferença entre dois saldos entregaria o
        # lançamento escondido.
        self.assertEqual(services.saldo_da_conta(self.conta, usuario=self.ana), Decimal("1000.00"))
        com_conta = self._lancar(self.ana, "50", "Café só meu", False, conta="Conjunta")
        self.assertIsNotNone(com_conta.conta)
        self.assertEqual(services.saldo_da_conta(self.conta, usuario=self.ana), Decimal("950.00"))
        self.assertEqual(services.saldo_da_conta(self.conta, usuario=self.bia), Decimal("1000.00"))

    def test_rosca_nao_mostra_categoria_so_do_outro(self):
        nomes = [nome for nome, _ in self._resumo(self.bia).por_categoria]
        self.assertFalse(any("Presentes" in n for n in nomes))


class LimiteTest(BaseCasalTest):
    """O limite é do espaço, mas o consumo é o de quem está olhando."""

    def setUp(self):
        super().setUp()
        self.limite = services.criar_limite(espaco=self.espaco, valor="500", categoria="Mercado")
        self._lancar(self.ana, "200", "Mercado da casa", True, categoria="Mercado")
        self._lancar(self.ana, "300", "Mercado só meu", False, categoria="Mercado")

    def test_quem_gastou_ve_a_propria_fatia_mais_o_proprio(self):
        # Metade dos R$ 200 da casa, mais os R$ 300 pessoais.
        consumo = services.consumo_do_limite(self.limite, usuario=self.ana)
        self.assertEqual(consumo["gasto"], Decimal("400.00"))
        self.assertEqual(consumo["percentual"], 80)

    def test_o_outro_ve_so_a_fatia_do_compartilhado(self):
        # Ver o número da Ana seria ver o gasto pessoal da Ana.
        consumo = services.consumo_do_limite(self.limite, usuario=self.bia)
        self.assertEqual(consumo["gasto"], Decimal("100.00"))
        self.assertEqual(consumo["percentual"], 20)

    def test_quem_usa_sozinho_ve_tudo_no_limite(self):
        # É o caso que derruba a regra "limite conta só o compartilhado": com o
        # padrão pessoal, o limite ficaria parado em zero para sempre.
        sozinha = Espaco.objects.create(nome="Só eu")
        semear_categorias(sozinha)
        cris = Usuario.objects.create_user(
            username="cris", email="cris@exemplo.com", password="x", espaco=sozinha
        )
        limite = services.criar_limite(espaco=sozinha, valor="300", categoria="Delivery")
        services.registrar_transacao(
            espaco=sozinha, valor="120", descricao="iFood", categoria="Delivery", autor=cris
        )
        self.assertEqual(
            services.consumo_do_limite(limite, usuario=cris)["gasto"], Decimal("120.00")
        )


class RecorrenteCompartilhadoTest(BaseCasalTest):
    def test_previsao_herda_o_recorte_do_recorrente(self):
        regra = services.criar_recorrente(
            espaco=self.espaco,
            descricao="Salário da Ana",
            valor="4000",
            dia_do_mes=5,
            tipo=TipoTransacao.RECEITA,
            autor=self.ana,
            compartilhada=False,
        )
        regra.inicio = date(2020, 1, 1)
        regra.save(update_fields=["inicio"])
        services.projetar_recorrentes(self.espaco, meses=1)

        prevista = Transacao.objects.get(prevista=True)
        self.assertFalse(prevista.compartilhada)
        self.assertEqual(prevista.autor, self.ana)

        self.assertEqual(
            services.saldo_previsto(self.espaco, usuario=self.ana)["a_receber"],
            Decimal("4000.00"),
        )
        self.assertEqual(
            services.saldo_previsto(self.espaco, usuario=self.bia)["a_receber"],
            Decimal("0"),
        )


class PortalTest(BaseCasalTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.bia)

    def test_painel_nao_mostra_o_pessoal_do_outro(self):
        self._lancar(self.ana, "120", "Presente surpresa", False, categoria="Presentes")
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertNotContains(resposta, "Presente surpresa")

    def test_tabela_marca_o_que_e_so_meu(self):
        self._lancar(self.bia, "80", "Livro da Bia", False, categoria="Educação")
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertContains(resposta, "só eu")

    def test_tabela_mostra_quem_lancou_o_compartilhado(self):
        self._lancar(self.ana, "300", "Mercado", True, categoria="Mercado")
        self.assertContains(self.client.get(reverse("carteira:painel")), "Ana")

    def test_csv_nao_exporta_o_pessoal_do_outro(self):
        self._lancar(self.ana, "120", "Presente surpresa", False)
        self._lancar(self.bia, "80", "Livro da Bia", False)
        corpo = self.client.get(reverse("carteira:exportar")).content.decode("utf-8")
        self.assertNotIn("Presente surpresa", corpo)
        self.assertIn("Livro da Bia", corpo)
        self.assertIn("quem_ve", corpo)

    def test_nao_edita_o_pessoal_do_outro(self):
        alheio = self._lancar(self.ana, "120", "Presente surpresa", False)
        resposta = self.client.get(reverse("carteira:editar_transacao", args=[alheio.codigo]))
        self.assertEqual(resposta.status_code, 404)

    def test_nao_apaga_o_pessoal_do_outro(self):
        alheio = self._lancar(self.ana, "120", "Presente surpresa", False)
        self.client.post(reverse("carteira:excluir_transacao", args=[alheio.codigo]))
        self.assertTrue(Transacao.objects.filter(pk=alheio.pk).exists())

    def test_lancar_como_so_meu(self):
        self.client.post(
            reverse("carteira:nova_transacao"),
            {
                "tipo": TipoTransacao.DESPESA,
                "valor": "45",
                "descricao": "Presente",
                "data": timezone.localdate().isoformat(),
                "categoria": "",
                "conta": "",
                "pago": "on",
                "compartilhada": "0",
                "modo_rateio": "",
            },
        )
        transacao = Transacao.objects.get(descricao="Presente")
        self.assertFalse(transacao.compartilhada)
        self.assertEqual(transacao.autor, self.bia)

    def test_espaco_de_uma_pessoa_nao_mostra_a_escolha(self):
        # Sem ninguém com quem dividir, a pergunta não tem sentido.
        sozinha = Espaco.objects.create(nome="Só eu")
        semear_categorias(sozinha)
        cris = Usuario.objects.create_user(
            username="cris", email="cris@exemplo.com", password="x", espaco=sozinha
        )
        self.client.force_login(cris)
        resposta = self.client.get(reverse("carteira:nova_transacao"))
        self.assertNotContains(resposta, "Quem vê")
        self.assertContains(resposta, 'type="hidden"')


class ConviteTest(TestCase):
    def setUp(self):
        self.casa = Espaco.objects.create(nome="Casa")
        semear_categorias(self.casa)
        self.ana = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.casa
        )
        self.sozinha = Espaco.objects.create(nome="Meu espaço")
        semear_categorias(self.sozinha)
        self.bia = Usuario.objects.create_user(
            username="bia",
            email="bia@exemplo.com",
            password="x",
            first_name="Bia",
            espaco=self.sozinha,
        )

    def test_convite_e_reaproveitado(self):
        # Cada recarga da tela geraria um código novo, e o que a pessoa já
        # mandou pararia de funcionar.
        primeiro = espacos.convite_vigente(self.casa, self.ana)
        self.assertEqual(espacos.convite_vigente(self.casa, self.ana).pk, primeiro.pk)

    def test_convite_expirado_da_lugar_a_um_novo(self):
        velho = ConviteEspaco.objects.create(
            espaco=self.casa,
            criado_por=self.ana,
            expira_em=timezone.now() - timedelta(minutes=1),
        )
        self.assertNotEqual(espacos.convite_vigente(self.casa, self.ana).pk, velho.pk)

    def test_entrar_leva_os_lancamentos_como_pessoais(self):
        # Trazê-los como compartilhados jogaria o extrato inteiro da pessoa na
        # cara de quem convidou.
        antigo = services.registrar_transacao(
            espaco=self.sozinha,
            valor="99",
            descricao="Coisa antiga",
            categoria="Lazer",
            autor=self.bia,
        )
        convite = espacos.convite_vigente(self.casa, self.ana)
        espacos.entrar_com_codigo(self.bia, convite.codigo)

        self.bia.refresh_from_db()
        self.assertEqual(self.bia.espaco, self.casa)
        antigo.refresh_from_db()
        self.assertEqual(antigo.espaco, self.casa)
        self.assertFalse(antigo.compartilhada)
        self.assertEqual(antigo.autor, self.bia)

    def test_entrar_reaproveita_categoria_pelo_nome(self):
        services.registrar_transacao(
            espaco=self.sozinha, valor="10", descricao="x", categoria="Mercado", autor=self.bia
        )
        antes = self.casa.categorias.count()
        espacos.entrar_com_codigo(self.bia, espacos.convite_vigente(self.casa, self.ana).codigo)
        self.assertEqual(self.casa.categorias.count(), antes)

    def test_entrar_renomeia_conta_com_nome_repetido(self):
        Conta.objects.create(espaco=self.casa, nome="Nubank")
        Conta.objects.create(espaco=self.sozinha, nome="Nubank")
        espacos.entrar_com_codigo(self.bia, espacos.convite_vigente(self.casa, self.ana).codigo)

        nomes = set(Conta.objects.filter(espaco=self.casa).values_list("nome", flat=True))
        self.assertIn("Nubank", nomes)
        self.assertEqual(len(nomes), 2)

    def test_espaco_vazio_e_apagado(self):
        espacos.entrar_com_codigo(self.bia, espacos.convite_vigente(self.casa, self.ana).codigo)
        self.assertFalse(Espaco.objects.filter(pk=self.sozinha.pk).exists())

    def test_convite_so_serve_uma_vez(self):
        convite = espacos.convite_vigente(self.casa, self.ana)
        espacos.entrar_com_codigo(self.bia, convite.codigo)
        cris = Usuario.objects.create_user(username="cris", email="cris@exemplo.com", password="x")
        with self.assertRaises(espacos.ErroDeEspaco):
            espacos.entrar_com_codigo(cris, convite.codigo)

    def test_codigo_invalido(self):
        with self.assertRaises(espacos.ErroDeEspaco):
            espacos.entrar_com_codigo(self.bia, "ZZZZZZ")

    def test_entrar_no_proprio_espaco_e_recusado(self):
        convite = espacos.convite_vigente(self.casa, self.ana)
        with self.assertRaises(espacos.ErroDeEspaco):
            espacos.entrar_com_codigo(self.ana, convite.codigo)

    def test_limite_acompanha_a_mudanca(self):
        services.criar_limite(espaco=self.sozinha, valor="300", categoria="Delivery")
        espacos.entrar_com_codigo(self.bia, espacos.convite_vigente(self.casa, self.ana).codigo)
        self.assertEqual(Limite.objects.get().espaco, self.casa)


class SairTest(BaseCasalTest):
    def test_leva_o_pessoal_e_deixa_o_compartilhado(self):
        compartilhado = self._lancar(self.ana, "300", "Mercado da casa", True)
        meu = self._lancar(self.bia, "80", "Livro da Bia", False)

        novo = espacos.sair_do_espaco(self.bia)

        self.bia.refresh_from_db()
        self.assertEqual(self.bia.espaco, novo)
        compartilhado.refresh_from_db()
        self.assertEqual(compartilhado.espaco, self.espaco)
        meu.refresh_from_db()
        self.assertEqual(meu.espaco, novo)

    def test_quem_esta_sozinho_nao_tem_de_onde_sair(self):
        sozinha = Espaco.objects.create(nome="Só eu")
        cris = Usuario.objects.create_user(
            username="cris", email="cris@exemplo.com", password="x", espaco=sozinha
        )
        with self.assertRaises(espacos.ErroDeEspaco):
            espacos.sair_do_espaco(cris)


class TelaCompartilharTest(BaseCasalTest):
    def test_mostra_membros_e_codigo(self):
        self.client.force_login(self.ana)
        resposta = self.client.get(reverse("carteira:compartilhar"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Bia")
        self.assertContains(resposta, ConviteEspaco.objects.get().codigo)

    def test_codigo_errado_explica_sem_quebrar(self):
        self.client.force_login(self.ana)
        resposta = self.client.post(reverse("carteira:compartilhar"), {"codigo": "ZZZZZZ"})
        self.assertContains(resposta, "não vale mais")

    def test_sair_pela_tela(self):
        self.client.force_login(self.bia)
        self.client.post(reverse("carteira:sair_do_espaco"))
        self.bia.refresh_from_db()
        self.assertNotEqual(self.bia.espaco, self.espaco)


class AgenteTest(BaseCasalTest):
    def test_o_agente_so_enxerga_o_que_a_pessoa_ve(self):
        from ai import tools
        from ai.agente import Contexto

        self._lancar(self.ana, "120", "Presente surpresa", False, categoria="Presentes")
        self._lancar(self.ana, "300", "Mercado da casa", True, categoria="Mercado")

        saida = tools.executar(
            "consultar_periodo",
            {
                "inicio": services.limites_do_mes()[0].isoformat(),
                "fim": services.limites_do_mes()[1].isoformat(),
                "categoria": "",
            },
            Contexto(espaco=self.espaco, usuario=self.bia),
        )
        # A fatia da Bia no mercado da casa é metade de R$ 300.
        self.assertIn("R$ 150,00", saida)
        self.assertNotIn("120", saida)

    def test_o_agente_registra_como_so_meu_quando_pedido(self):
        from ai import tools
        from ai.agente import Contexto

        tools.executar(
            "registrar_transacao",
            {
                "valor": 45,
                "descricao": "Presente",
                "tipo": "despesa",
                "categoria": "Presentes",
                "conta": "",
                "data": timezone.localdate().isoformat(),
                "pago": True,
                "compartilhada": False,
            },
            Contexto(espaco=self.espaco, usuario=self.bia),
        )
        transacao = Transacao.objects.get(descricao="Presente")
        self.assertFalse(transacao.compartilhada)
        self.assertEqual(transacao.autor, self.bia)

    def test_o_prompt_diz_com_quem_o_espaco_e_dividido(self):
        from ai.agente import Contexto, responder
        from ai.fakes import ClienteFalso

        cliente = ClienteFalso().responde("oi")
        responder(Contexto(espaco=self.espaco, usuario=self.ana), "oi", cliente=cliente)
        volatil = cliente.ultima_chamada["system"][1]["text"]
        self.assertIn("Bia", volatil)
        self.assertIn("compartilhada=False", volatil)

    def test_espaco_de_uma_pessoa_orienta_o_contrario(self):
        from ai.agente import Contexto, responder
        from ai.fakes import ClienteFalso

        sozinha = Espaco.objects.create(nome="Só eu")
        cris = Usuario.objects.create_user(
            username="cris", email="cris@exemplo.com", password="x", espaco=sozinha
        )
        cliente = ClienteFalso().responde("oi")
        responder(Contexto(espaco=sozinha, usuario=cris), "oi", cliente=cliente)
        self.assertIn("Só esta pessoa usa o espaço", cliente.ultima_chamada["system"][1]["text"])


class AlertaProativoTest(BaseCasalTest):
    """Os alertas saem por iniciativa nossa: um erro aqui não é tela quebrada,
    é o gasto pessoal de alguém chegando no WhatsApp de outra pessoa."""

    def setUp(self):
        super().setUp()
        from unittest import mock

        from zap.canais.fake import FakeCanal
        from zap.models import JanelaAtendimento, NumeroWhatsApp

        self.canal = FakeCanal()
        patch = mock.patch("zap.janela.obter_canal", return_value=self.canal)
        patch.start()
        self.addCleanup(patch.stop)

        self.numeros = {}
        for pessoa, numero in ((self.ana, "5511999998888"), (self.bia, "5511888887777")):
            n = NumeroWhatsApp.objects.create(
                numero=numero, usuario=pessoa, verificado_em=timezone.now()
            )
            JanelaAtendimento.objects.create(numero=n, ultimo_inbound_em=timezone.now())
            self.numeros[pessoa] = n

    def _destinos(self):
        return {e["destino"] for e in self.canal.enviadas}

    def test_vencimento_pessoal_so_avisa_quem_lancou(self):
        from carteira.tasks import lembrar_vencimentos

        self._lancar(
            self.ana,
            "200",
            "Presente de aniversário",
            False,
            data_lancamento=timezone.localdate(),
            pago=False,
        )
        lembrar_vencimentos()

        self.assertEqual(self._destinos(), {self.numeros[self.ana].numero})
        self.assertIn("Presente de aniversário", self.canal.ultimo_texto)

    def test_vencimento_compartilhado_avisa_os_dois(self):
        from carteira.tasks import lembrar_vencimentos

        self._lancar(
            self.ana,
            "310",
            "Conta de luz",
            True,
            data_lancamento=timezone.localdate(),
            pago=False,
        )
        lembrar_vencimentos()
        self.assertEqual(len(self._destinos()), 2)

    def test_resumo_semanal_e_por_pessoa(self):
        from carteira.tasks import resumo_semanal

        self._lancar(self.ana, "500", "Coisa da Ana", False, categoria="Presentes")
        self._lancar(self.bia, "100", "Mercado", True, categoria="Mercado")
        self.assertEqual(resumo_semanal(), 2)

        por_destino = {e["destino"]: e["texto"] for e in self.canal.enviadas}
        # Ana: 500 pessoais + metade dos 100 da casa. Bia: só a metade dela.
        self.assertIn("R$ 550,00", por_destino[self.numeros[self.ana].numero])
        self.assertIn("R$ 50,00", por_destino[self.numeros[self.bia].numero])
        # O total da Bia não pode conter o gasto pessoal da Ana.
        self.assertNotIn("R$ 550,00", por_destino[self.numeros[self.bia].numero])

    def test_alerta_de_limite_vai_so_para_quem_gastou(self):
        # O percentual é o número de quem olha: mandá-lo a todos entregaria o
        # gasto pessoal — "você usou 80% de R$ 400" deixa deduzir os R$ 320.
        from carteira.tasks import verificar_limites

        services.criar_limite(espaco=self.espaco, valor="100", categoria="Presentes")
        self._lancar(self.ana, "500", "Presente caro", False, categoria="Presentes")

        self.assertEqual(verificar_limites(), 1)
        self.assertEqual(self._destinos(), {self.numeros[self.ana].numero})
        self.assertIn("passou do limite", self.canal.ultimo_texto)

    def test_alerta_de_limite_compartilhado_vai_para_os_dois(self):
        from carteira.tasks import verificar_limites

        services.criar_limite(espaco=self.espaco, valor="100", categoria="Presentes")
        self._lancar(self.ana, "500", "Presente da casa", True, categoria="Presentes")

        self.assertEqual(verificar_limites(), 2)
        self.assertEqual(len(self._destinos()), 2)


class PadraoPessoalTest(BaseCasalTest):
    """Compartilhar é a escolha ativa: errar guardando não custa nada, errar
    expondo não tem desfazer."""

    def test_lancamento_nasce_pessoal(self):
        transacao = services.registrar_transacao(
            espaco=self.espaco, valor="50", descricao="Café", autor=self.ana
        )
        self.assertFalse(transacao.compartilhada)

    def test_recorrente_nasce_pessoal(self):
        regra = services.criar_recorrente(
            espaco=self.espaco,
            descricao="Academia",
            valor="120",
            dia_do_mes=10,
            autor=self.ana,
        )
        self.assertFalse(regra.compartilhada)

    def test_o_formulario_ja_vem_em_so_eu(self):
        from carteira.forms import TransacaoForm

        html = str(TransacaoForm(espaco=self.espaco)["compartilhada"])
        # A opção marcada é a primeira, "Só eu" (value 0).
        self.assertIn('value="0" required id="id_compartilhada_0" checked', html)

    def test_o_agente_registra_como_pessoal_sem_pedirem(self):
        from ai import tools
        from ai.agente import Contexto

        tools.executar(
            "registrar_transacao",
            {
                "valor": 30,
                "descricao": "Almoço",
                "tipo": "despesa",
                "categoria": "Alimentação",
                "conta": "",
                "data": timezone.localdate().isoformat(),
                "pago": True,
                "compartilhada": False,
            },
            Contexto(espaco=self.espaco, usuario=self.ana),
        )
        self.assertFalse(Transacao.objects.get(descricao="Almoço").compartilhada)

    def test_o_prompt_manda_o_padrao_pessoal(self):
        from ai.agente import Contexto, responder
        from ai.fakes import ClienteFalso

        cliente = ClienteFalso().responde("oi")
        responder(Contexto(espaco=self.espaco, usuario=self.ana), "oi", cliente=cliente)
        self.assertIn("PESSOAL por padrão", cliente.ultima_chamada["system"][1]["text"])


class HistoricoAoEntrarTest(TestCase):
    """Convidar é para dividir daqui pra frente, não para abrir o passado."""

    def setUp(self):
        self.casa = Espaco.objects.create(nome="Casa")
        semear_categorias(self.casa)
        self.ana = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.casa
        )
        self.bia = Usuario.objects.create_user(
            username="bia",
            email="bia@exemplo.com",
            password="x",
        )

    def test_o_que_ja_existia_vira_pessoal_de_quem_convidou(self):
        antigo = services.registrar_transacao(
            espaco=self.casa, valor="900", descricao="Coisa antiga", autor=self.ana
        )
        Transacao.objects.filter(pk=antigo.pk).update(compartilhada=True)

        espacos.entrar_com_codigo(self.bia, espacos.convite_vigente(self.casa, self.ana).codigo)

        antigo.refresh_from_db()
        self.assertFalse(antigo.compartilhada)
        self.assertEqual(antigo.autor, self.ana)

    def test_historico_sem_autor_ganha_o_dono_do_espaco(self):
        # Registro vindo de importação ou de comando antigo, sem dono. Hoje o
        # serviço presume o dono num espaço de uma pessoa; este caso cobre o
        # dado que já estava no banco antes disso.
        orfa = services.registrar_transacao(espaco=self.casa, valor="10", descricao="Sem autor")
        Transacao.objects.filter(pk=orfa.pk).update(autor=None, compartilhada=True)
        orfa.refresh_from_db()
        self.assertIsNone(orfa.autor)

        espacos.entrar_com_codigo(self.bia, espacos.convite_vigente(self.casa, self.ana).codigo)
        orfa.refresh_from_db()
        self.assertEqual(orfa.autor, self.ana)
        self.assertFalse(orfa.compartilhada)

    def test_recorrente_antigo_tambem_vira_pessoal(self):
        regra = services.criar_recorrente(
            espaco=self.casa,
            descricao="Aluguel",
            valor="1800",
            dia_do_mes=5,
            autor=self.ana,
            compartilhada=True,
        )
        espacos.entrar_com_codigo(self.bia, espacos.convite_vigente(self.casa, self.ana).codigo)
        regra.refresh_from_db()
        self.assertFalse(regra.compartilhada)

    def test_a_terceira_pessoa_nao_desfaz_escolhas_ja_feitas(self):
        # Com duas pessoas dentro, o que está compartilhado foi marcado de
        # propósito; entrar uma terceira não pode reverter isso.
        espacos.entrar_com_codigo(self.bia, espacos.convite_vigente(self.casa, self.ana).codigo)
        combinado = services.registrar_transacao(
            espaco=self.casa,
            valor="310",
            descricao="Conta de luz",
            autor=self.ana,
            compartilhada=True,
        )
        cris = Usuario.objects.create_user(username="cris", email="cris@exemplo.com", password="x")
        espacos.entrar_com_codigo(cris, espacos.convite_vigente(self.casa, self.ana).codigo)
        combinado.refresh_from_db()
        self.assertTrue(combinado.compartilhada)


class DonoPresumidoTest(TestCase):
    """Um lançamento pessoal sem dono não é de ninguém e some para todos — a
    armadilha do padrão pessoal. Fechada na criação."""

    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)

    def test_espaco_de_uma_pessoa_atribui_a_ela(self):
        dono = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        transacao = services.registrar_transacao(
            espaco=self.espaco, valor="10", descricao="Sem autor informado"
        )
        self.assertEqual(transacao.autor, dono)
        self.assertIn(
            transacao,
            services.visiveis_para(Transacao.objects.all(), dono),
        )

    def test_com_duas_pessoas_nao_se_adivinha(self):
        # Chutar o dono seria mostrar o gasto de alguém a quem não deve vê-lo.
        ana = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        Usuario.objects.create_user(
            username="bia", email="bia@exemplo.com", password="x", espaco=self.espaco
        )
        transacao = services.registrar_transacao(
            espaco=self.espaco, valor="10", descricao="Sem autor informado"
        )
        self.assertIsNone(transacao.autor)
        self.assertNotIn(transacao, services.visiveis_para(Transacao.objects.all(), ana))

    def test_autor_informado_manda(self):
        ana = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        bia = Usuario.objects.create_user(
            username="bia", email="bia@exemplo.com", password="x", espaco=self.espaco
        )
        transacao = services.registrar_transacao(
            espaco=self.espaco, valor="10", descricao="x", autor=bia
        )
        self.assertEqual(transacao.autor, bia)
        self.assertNotEqual(transacao.autor, ana)
