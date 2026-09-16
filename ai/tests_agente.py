"""O agente e suas ferramentas, contra um cliente Claude falso."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from accounts.models import Espaco, Usuario
from ai import tools
from ai.agente import Contexto, SemQuota, responder
from ai.fakes import ClienteFalso
from carteira.models import Conta, Limite, Recorrente, TipoTransacao, Transacao
from carteira.seeds import semear_categorias


class BaseAgenteTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        Conta.objects.create(espaco=self.espaco, nome="Nubank")
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.contexto = Contexto(espaco=self.espaco, usuario=self.usuario, hoje=date(2026, 9, 14))


class LoopTest(BaseAgenteTest):
    def test_resposta_sem_ferramenta(self):
        cliente = ClienteFalso().responde("Oi! Como posso ajudar? 💜")
        r = responder(self.contexto, "oi", cliente=cliente)
        self.assertEqual(r.texto, "Oi! Como posso ajudar? 💜")
        self.assertEqual(r.iteracoes, 1)
        self.assertEqual(r.ferramentas_usadas, [])

    def test_registra_gasto_e_confirma(self):
        cliente = (
            ClienteFalso()
            .chama(
                "registrar_transacao",
                valor=34,
                descricao="Uber",
                tipo="despesa",
                categoria="Transporte",
                conta="Nubank",
                data="2026-09-14",
                pago=True,
            )
            .responde("Registrei: Uber, R$ 34,00 em Transporte ✅")
        )
        r = responder(self.contexto, "uber 34 reais", cliente=cliente)

        transacao = Transacao.objects.get()
        self.assertEqual(transacao.valor, Decimal("34.00"))
        self.assertEqual(transacao.categoria.nome, "Transporte")
        self.assertEqual(transacao.conta.nome, "Nubank")
        self.assertEqual(transacao.autor, self.usuario)
        self.assertEqual(r.ferramentas_usadas, ["registrar_transacao"])
        self.assertEqual(r.iteracoes, 2)

    def test_varias_tools_numa_resposta_so(self):
        # A API pode devolver vários tool_use de uma vez. Todos os tool_result
        # precisam voltar numa ÚNICA mensagem, senão o modelo aprende a parar
        # de chamar em paralelo.
        cliente = (
            ClienteFalso()
            .chama_varias(
                (
                    "registrar_transacao",
                    {
                        "valor": 32,
                        "descricao": "Almoço",
                        "tipo": "despesa",
                        "categoria": "Alimentação",
                        "conta": "",
                        "data": "2026-09-14",
                        "pago": True,
                    },
                ),
                (
                    "registrar_transacao",
                    {
                        "valor": 19.9,
                        "descricao": "Uber",
                        "tipo": "despesa",
                        "categoria": "Transporte",
                        "conta": "",
                        "data": "2026-09-14",
                        "pago": True,
                    },
                ),
            )
            .responde("Registrei os dois ✅")
        )

        responder(self.contexto, "almocei 32 e paguei 19,90 de uber", cliente=cliente)

        self.assertEqual(Transacao.objects.count(), 2)
        resultados = cliente.resultados_enviados()
        self.assertEqual(len(resultados), 2)
        self.assertEqual({r["type"] for r in resultados}, {"tool_result"})

    def test_erro_de_dominio_volta_como_tool_result_e_nao_estoura(self):
        # A Claude precisa LER o motivo para explicar à pessoa ou tentar de
        # outro jeito — por isso o erro vira resultado, não exceção.
        cliente = (
            ClienteFalso()
            .chama("excluir_transacao", codigo="ZZZZZ")
            .responde("Não achei esse lançamento 🤔")
        )
        r = responder(self.contexto, "apaga o ZZZZZ", cliente=cliente)

        resultado = cliente.resultados_enviados()[0]
        self.assertTrue(resultado["is_error"])
        self.assertIn("ZZZZZ", resultado["content"])
        self.assertEqual(r.texto, "Não achei esse lançamento 🤔")

    def test_ferramenta_desconhecida_nao_derruba_o_loop(self):
        cliente = ClienteFalso().chama("formatar_disco").responde("Não sei fazer isso.")
        responder(self.contexto, "x", cliente=cliente)
        self.assertTrue(cliente.resultados_enviados()[0]["is_error"])

    @override_settings(AI_MAX_ITERACOES=3)
    def test_teto_de_iteracoes_corta_o_laco(self):
        # Uma tool falhando em laço não pode queimar a quota da pessoa.
        cliente = ClienteFalso()
        for _ in range(3):
            cliente.chama("consultar_limites")
        r = responder(self.contexto, "e aí?", cliente=cliente)
        self.assertEqual(r.iteracoes, 3)
        self.assertIn("embananei", r.texto)


class QuotaTest(BaseAgenteTest):
    def test_consumo_e_registrado_a_cada_chamada(self):
        cliente = ClienteFalso().responde("oi")
        responder(self.contexto, "oi", cliente=cliente)
        consumo = self.usuario.consumos_ia.get()
        self.assertEqual(consumo.tokens_entrada, 100)
        self.assertEqual(consumo.tokens_saida, 50)
        self.assertGreater(consumo.custo_usd, 0)

    @override_settings(QUOTA_TOKENS_DEFAULT=10)
    def test_sem_quota_recusa_antes_de_chamar_a_api(self):
        from accounts.models import ConsumoIA

        ConsumoIA.objects.create(usuario=self.usuario, modelo="claude-opus-5", tokens_entrada=50)
        cliente = ClienteFalso().responde("oi")
        with self.assertRaises(SemQuota):
            responder(self.contexto, "oi", cliente=cliente)
        self.assertEqual(cliente.chamadas, [])


class PromptTest(BaseAgenteTest):
    def test_o_breakpoint_de_cache_fica_no_bloco_estavel(self):
        # Se o cache_control ficasse no último bloco, a data de hoje entraria
        # no prefixo e o cache seria invalidado toda meia-noite.
        cliente = ClienteFalso().responde("oi")
        responder(self.contexto, "oi", cliente=cliente)

        sistema = cliente.ultima_chamada["system"]
        self.assertEqual(len(sistema), 2)
        self.assertIn("cache_control", sistema[0])
        self.assertNotIn("cache_control", sistema[1])
        self.assertNotIn("14/09/2026", sistema[0]["text"])
        self.assertIn("14/09/2026", sistema[1]["text"])

    def test_contexto_lista_categorias_e_contas_do_espaco(self):
        cliente = ClienteFalso().responde("oi")
        responder(self.contexto, "oi", cliente=cliente)
        volatil = cliente.ultima_chamada["system"][1]["text"]
        self.assertIn("Alimentação", volatil)
        self.assertIn("Nubank", volatil)

    def test_esforco_baixo_no_registro_e_alto_na_analise(self):
        registro = ClienteFalso().responde("ok")
        responder(self.contexto, "oi", cliente=registro)
        self.assertEqual(registro.ultima_chamada["output_config"]["effort"], "low")

        analise = ClienteFalso().chama("consultar_planejamento").responde("Dá pra comprar 👍")
        responder(self.contexto, "posso comprar um tênis de 420?", cliente=analise)
        self.assertEqual(analise.ultima_chamada["output_config"]["effort"], "high")

    def test_todas_as_tools_sao_declaradas(self):
        cliente = ClienteFalso().responde("oi")
        responder(self.contexto, "oi", cliente=cliente)
        enviadas = {t["name"] for t in cliente.ultima_chamada["tools"]}
        self.assertEqual(enviadas, {t["name"] for t in tools.TOOLS})


class SchemaTest(TestCase):
    """A API só garante os argumentos se o schema estiver fechado."""

    def test_toda_tool_tem_schema_fechado(self):
        for tool in tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertFalse(tool["input_schema"]["additionalProperties"])

    def test_strict_fica_onde_um_argumento_torto_gravaria_errado(self):
        """O orçamento de complexidade do `strict` é agregado sobre todas as
        tools da requisição e não cabe para todas (400 "Schema is too
        complex."). Ele é gasto onde um argumento inválido gravaria dinheiro
        errado; numa consulta, o pior caso é uma leitura ruim.

        `excluir_transacao` é a exceção medida entre as de escrita: recebe só
        um código, e um valor torto já cai no "não achei". Contra o risco real
        — apagar o lançamento ERRADO — o `strict` nunca protegeu, porque
        valida formato e não semântica.
        """
        sem_strict = tools.SOMENTE_LEITURA | {"excluir_transacao"}
        for tool in tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertEqual(tool.get("strict", False), tool["name"] not in sem_strict)

    def test_no_maximo_cinco_tools_strict(self):
        """Medido contra a API em 16/09/2026, com as nove tools atuais: cinco
        strict passam, seis dão 400 "Schema is too complex.".

        O teto anterior era seis, medido quando o conjunto tinha oito tools. O
        orçamento é agregado sobre TODAS as tools da requisição, não só as
        strict — então ele encolhe quando uma tool nova entra, mesmo sem
        strict. Ao adicionar tool, meça de novo em vez de confiar no número.
        """
        quantas = sum(1 for t in tools.TOOLS if t.get("strict"))
        self.assertLessEqual(quantas, 5)

    def test_required_traz_so_o_que_e_mesmo_obrigatorio(self):
        """`strict` NÃO exige que todo campo esteja em `required`.

        Acreditar que exigia custou caro: com os seis campos de
        `editar_transacao` obrigatórios, mudar só o valor obrigava o modelo a
        inventar um valor para os outros quatro. Em produção ele não conseguiu
        — entrou em laço cuspindo sintaxe corrompida até estourar o teto de
        iterações, e numa outra tentativa simplesmente não chamou a tool.
        Medido contra a API: `required` parcial é aceito e o modelo passa a
        mandar só os campos que importam.

        O que continua valendo é `required ⊆ properties`.
        """
        for tool in tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                esquema = tool["input_schema"]
                self.assertTrue(set(esquema["required"]) <= set(esquema["properties"]))

    def test_editar_transacao_exige_so_o_codigo(self):
        editar = next(t for t in tools.TOOLS if t["name"] == "editar_transacao")
        self.assertEqual(editar["input_schema"]["required"], ["codigo"])

    def test_nenhum_schema_usa_validador_recusado_pela_api(self):
        """Sob `strict: True` a API recusa alguns validadores do JSON Schema.

        Descoberto numa chamada real: `minimum`/`maximum` num campo `integer`
        derrubam a requisição INTEIRA com 400, e não só aquela ferramenta —
        nenhum teste com cliente falso pegaria isso, porque a validação
        acontece no servidor.
        """
        proibidos = {
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "multipleOf",
            "minLength",
            "maxLength",
            "pattern",
            "minItems",
            "maxItems",
            "uniqueItems",
        }
        for tool in tools.TOOLS:
            for campo, esquema in tool["input_schema"]["properties"].items():
                usados = proibidos & set(esquema)
                with self.subTest(tool=tool["name"], campo=campo):
                    self.assertFalse(usados, f"validador não suportado: {usados}")

    def test_toda_tool_declarada_tem_manipulador(self):
        for tool in tools.TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertIn(tool["name"], tools._MANIPULADORES)


class ToolsTest(BaseAgenteTest):
    def test_consultar_periodo_resume(self):
        from carteira import services

        services.registrar_transacao(
            espaco=self.espaco,
            valor="247,80",
            descricao="Mercado",
            categoria="Mercado",
            data_lancamento=date(2026, 9, 10),
        )
        saida = tools.executar(
            "consultar_periodo",
            {"inicio": "2026-09-01", "fim": "2026-09-30", "categoria": ""},
            self.contexto,
        )
        self.assertIn("R$ 247,80", saida)
        self.assertIn("Mercado", saida)

    def test_consultar_periodo_por_categoria(self):
        from carteira import services

        services.registrar_transacao(
            espaco=self.espaco,
            valor="100",
            descricao="Mercado",
            categoria="Mercado",
            data_lancamento=date(2026, 9, 10),
        )
        services.registrar_transacao(
            espaco=self.espaco,
            valor="50",
            descricao="iFood",
            categoria="Delivery",
            data_lancamento=date(2026, 9, 10),
        )
        saida = tools.executar(
            "consultar_periodo",
            {"inicio": "2026-09-01", "fim": "2026-09-30", "categoria": "Delivery"},
            self.contexto,
        )
        self.assertIn("R$ 50,00", saida)
        self.assertNotIn("100", saida)

    def test_criar_limite_temporario(self):
        saida = tools.executar(
            "criar_limite",
            {"valor": 200, "categoria": "Presentes", "rotulo": "", "dias": 10},
            self.contexto,
        )
        limite = Limite.objects.get()
        self.assertTrue(limite.temporario)
        self.assertIn("R$ 200,00", saida)

    def test_criar_recorrente_ja_projeta(self):
        tools.executar(
            "criar_recorrente",
            {
                "descricao": "Aluguel",
                "valor": 1800,
                "dia_do_mes": 30,
                "tipo": "despesa",
                "categoria": "Moradia",
                "conta": "",
            },
            self.contexto,
        )
        self.assertEqual(Recorrente.objects.count(), 1)
        self.assertTrue(Transacao.objects.filter(prevista=True).exists())

    def test_consultar_planejamento_materializa_antes_de_somar(self):
        # Sem isto, quem cadastrou um recorrente agora veria a projeção sem ele.
        Recorrente.objects.create(
            espaco=self.espaco,
            descricao="Aluguel",
            valor=Decimal("1800"),
            dia_do_mes=30,
            tipo=TipoTransacao.DESPESA,
            inicio=date(2020, 1, 1),
            # Recorrente nasce pessoal: sem autor, ninguém o enxergaria.
            autor=self.usuario,
        )
        saida = tools.executar("consultar_planejamento", {}, self.contexto)
        self.assertIn("R$ 1.800,00", saida)

    def test_consultar_limites_sem_nenhum(self):
        self.assertIn("Nenhum limite", tools.executar("consultar_limites", {}, self.contexto))

    def test_data_invalida_vira_erro_legivel(self):
        saida = tools.executar(
            "registrar_transacao",
            {
                "valor": 10,
                "descricao": "x",
                "tipo": "despesa",
                "categoria": "",
                "conta": "",
                "data": "14/09/2026",
                "pago": True,
            },
            self.contexto,
        )
        self.assertTrue(saida.startswith("ERRO:"))
        self.assertIn("Data inválida", saida)

    def test_formato_de_dinheiro_e_brasileiro(self):
        self.assertEqual(tools._dinheiro(Decimal("1234.56")), "R$ 1.234,56")
        self.assertEqual(tools._dinheiro(Decimal("34")), "R$ 34,00")
