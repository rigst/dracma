"""Alertas proativos: dedupe, alcance e conteúdo."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Espaco, Usuario
from carteira import services
from carteira.models import Alerta, TipoTransacao
from carteira.seeds import semear_categorias
from carteira.tasks import (
    lembrar_vencimentos,
    projetar_recorrentes,
    resumo_semanal,
    verificar_limites,
)
from bot.canais.fake import FakeCanal
from bot.models import ContaTelegram


class BaseAlertaTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.conta = ContaTelegram.objects.create(
            chat_id=987654321, usuario=self.usuario, verificado_em=timezone.now()
        )
        self.canal = FakeCanal()
        patch = mock.patch("bot.envio.obter_canal", return_value=self.canal)
        patch.start()
        self.addCleanup(patch.stop)

    def _gastar(self, valor, categoria="Delivery", autor=None):
        # `autor` importa: lançamento é pessoal por padrão, e o consumo do
        # limite é recortado pela visibilidade de quem está olhando.
        return services.registrar_transacao(
            espaco=self.espaco,
            valor=valor,
            descricao="iFood",
            categoria=categoria,
            autor=autor or self.usuario,
        )


class LimiteAlertaTest(BaseAlertaTest):
    def test_avisa_ao_passar_de_80_por_cento(self):
        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("250")
        self.assertEqual(verificar_limites(), 1)

        texto = self.canal.ultimo_texto
        self.assertIn("83%", texto)
        self.assertIn("Delivery", texto)
        self.assertIn("R$ 50,00", texto)

    def test_nao_avisa_abaixo_do_gatilho(self):
        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("100")
        self.assertEqual(verificar_limites(), 0)
        self.assertEqual(self.canal.enviadas, [])

    def test_avisa_o_estouro_com_o_excedente(self):
        services.criar_limite(espaco=self.espaco, valor="100", categoria="Delivery")
        self._gastar("150")
        verificar_limites()
        texto = self.canal.ultimo_texto
        self.assertIn("passou do limite", texto)
        self.assertIn("R$ 50,00", texto)

    def test_nao_repete_o_mesmo_aviso_na_rodada_seguinte(self):
        # A task roda de hora em hora: sem dedupe, seria o mesmo aviso toda
        # hora até o fim do mês.
        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("250")
        verificar_limites()
        self.assertEqual(verificar_limites(), 0)
        self.assertEqual(len(self.canal.enviadas), 1)

    def test_proximo_e_estouro_sao_avisos_distintos(self):
        limite = services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("250")
        verificar_limites()
        self._gastar("100")
        self.assertEqual(verificar_limites(), 1)
        # A chave do alerta carrega o membro: cada pessoa recebe o número dela.
        self.assertEqual(
            set(
                Alerta.objects.filter(chave__startswith=f"limite:{limite.pk}:").values_list(
                    "tipo", flat=True
                )
            ),
            {Alerta.Tipo.LIMITE_PROXIMO, Alerta.Tipo.LIMITE_ESTOURADO},
        )

    @override_settings(LIMITE_ALERTA_PERCENTUAL=50)
    def test_gatilho_vem_das_settings(self):
        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("160")
        self.assertEqual(verificar_limites(), 1)

    def test_gasto_compartilhado_avisa_todo_mundo_do_espaco(self):
        # Num casal, quem estourou o limite da casa pode não ser quem o criou.
        parceiro = Usuario.objects.create_user(
            username="bia", email="bia@exemplo.com", password="x", espaco=self.espaco
        )
        ContaTelegram.objects.create(
            chat_id=222000222, usuario=parceiro, verificado_em=timezone.now()
        )

        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        # Dividido ao meio: R$ 250 para cada, de um limite de R$ 300.
        services.registrar_transacao(
            espaco=self.espaco,
            valor="500",
            descricao="iFood da casa",
            categoria="Delivery",
            autor=self.usuario,
            compartilhada=True,
        )
        verificar_limites()
        self.assertEqual(len(self.canal.enviadas), 2)

    def test_gasto_pessoal_avisa_so_quem_gastou(self):
        # O percentual é o número de quem olha; mandá-lo aos dois entregaria o
        # gasto pessoal de um deles.
        parceiro = Usuario.objects.create_user(
            username="bia", email="bia@exemplo.com", password="x", espaco=self.espaco
        )
        ContaTelegram.objects.create(
            chat_id=222000222, usuario=parceiro, verificado_em=timezone.now()
        )

        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("250")  # pessoal, do self.usuario
        verificar_limites()
        self.assertEqual(len(self.canal.enviadas), 1)
        self.assertEqual(self.canal.enviadas[0]["destino"], str(self.conta.chat_id))


class BotBloqueadoTest(BaseAlertaTest):
    """O que sobrou de "o alerta não pode sair".

    No WhatsApp era a janela de 24h da Meta. No Telegram é só um caso: a pessoa
    bloqueou o bot. O alerta continua sendo registrado como adiado — ele conta
    como "já decidido neste período" e não repete a cada hora.
    """

    def _bloquear(self):
        self.conta.bloqueado_em = timezone.now()
        self.conta.save(update_fields=["bloqueado_em"])

    def test_conta_bloqueada_nao_recebe_e_o_alerta_fica_adiado(self):
        self._bloquear()
        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("250")

        self.assertEqual(verificar_limites(), 0)
        self.assertEqual(self.canal.enviadas, [])
        self.assertTrue(Alerta.objects.get().adiado)

    def test_alerta_adiado_nao_repete_na_proxima_rodada(self):
        self._bloquear()
        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("250")
        verificar_limites()
        verificar_limites()
        self.assertEqual(Alerta.objects.count(), 1)

    def test_sem_bloqueio_o_alerta_sai_sem_consultar_janela_nenhuma(self):
        # O ganho da migração: não há inbound recente, e mesmo assim sai.
        services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self._gastar("250")

        self.assertEqual(verificar_limites(), 1)
        self.assertIn("Delivery", self.canal.ultimo_texto)
        self.assertFalse(Alerta.objects.get().adiado)


class VencimentoTest(BaseAlertaTest):
    def _conta_para(self, dia):
        return services.registrar_transacao(
            espaco=self.espaco,
            valor="743,20",
            descricao="Cartão Itaú",
            categoria="Cartão de crédito",
            data_lancamento=dia,
            pago=False,
        )

    def test_avisa_o_que_vence_hoje(self):
        transacao = self._conta_para(timezone.localdate())
        self.assertEqual(lembrar_vencimentos(), 1)
        texto = self.canal.ultimo_texto
        self.assertIn("vence hoje", texto)
        self.assertIn("R$ 743,20", texto)

    def test_o_lembrete_nao_ensina_o_codigo(self):
        """O aviso dizia “paguei {código}”, o que treinava a pessoa a decorar
        cinco caracteres aleatórios para dar baixa. A assistente resolve qual
        é o lançamento pelo nome (ver `listar_transacoes`), então o código não
        precisa sair do portal."""
        transacao = self._conta_para(timezone.localdate())
        lembrar_vencimentos()
        texto = self.canal.ultimo_texto

        self.assertNotIn(transacao.codigo, texto)
        self.assertIn("paguei o cartão itaú", texto)

    def test_avisa_o_que_vence_amanha(self):
        self._conta_para(timezone.localdate() + timedelta(days=1))
        lembrar_vencimentos()
        self.assertIn("vence amanhã", self.canal.ultimo_texto)

    def test_nao_avisa_o_que_ja_foi_pago(self):
        services.registrar_transacao(
            espaco=self.espaco,
            valor="100",
            descricao="Luz",
            data_lancamento=timezone.localdate(),
            pago=True,
        )
        self.assertEqual(lembrar_vencimentos(), 0)

    def test_nao_avisa_o_que_vence_depois_de_amanha(self):
        self._conta_para(timezone.localdate() + timedelta(days=3))
        self.assertEqual(lembrar_vencimentos(), 0)

    def test_hoje_e_amanha_sao_avisos_distintos(self):
        # A mesma conta é avisada um dia antes E no dia; a referência é a data.
        transacao = self._conta_para(timezone.localdate() + timedelta(days=1))
        lembrar_vencimentos()
        transacao.data = timezone.localdate()
        transacao.save(update_fields=["data"])
        self.assertEqual(lembrar_vencimentos(), 1)


class ResumoSemanalTest(BaseAlertaTest):
    def test_resume_o_periodo(self):
        self._gastar("200", categoria="Mercado")
        services.registrar_transacao(
            espaco=self.espaco, valor="4200", descricao="Salário", tipo=TipoTransacao.RECEITA
        )
        self.assertEqual(resumo_semanal(), 1)

        texto = self.canal.ultimo_texto
        self.assertIn("R$ 4.200,00", texto)
        self.assertIn("Mercado", texto)

    def test_espaco_sem_movimento_nao_recebe_nada(self):
        self.assertEqual(resumo_semanal(), 0)
        self.assertEqual(self.canal.enviadas, [])

    def test_nao_repete_no_mesmo_dia(self):
        self._gastar("200")
        resumo_semanal()
        self.assertEqual(resumo_semanal(), 0)


class ProjecaoTest(BaseAlertaTest):
    def test_projeta_todos_os_espacos(self):
        regra = services.criar_recorrente(
            espaco=self.espaco, descricao="Aluguel", valor="1800", dia_do_mes=15
        )
        regra.inicio = timezone.localdate() - timedelta(days=365)
        regra.save(update_fields=["inicio"])
        self.assertGreater(projetar_recorrentes(), 0)


class SemContaTest(TestCase):
    def test_espaco_sem_telegram_conectado_nao_quebra(self):
        # Quem só usa o portal não conectou o Telegram: a task não pode estourar.
        espaco = Espaco.objects.create(nome="Só portal")
        semear_categorias(espaco)
        dono = Usuario.objects.create_user(
            username="zeca", email="zeca@exemplo.com", password="x", espaco=espaco
        )
        services.criar_limite(espaco=espaco, valor="100", categoria="Delivery")
        services.registrar_transacao(
            espaco=espaco, valor="90", descricao="iFood", categoria="Delivery", autor=dono
        )
        self.assertEqual(verificar_limites(), 0)
        self.assertTrue(Alerta.objects.get().adiado)


class DinheiroTest(TestCase):
    def test_formato_brasileiro(self):
        from carteira.tasks import _dinheiro

        self.assertEqual(_dinheiro(Decimal("1234.5")), "R$ 1.234,50")
