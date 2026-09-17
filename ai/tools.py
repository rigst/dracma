"""Ferramentas que a Claude pode chamar.

Cada tool é um contrato fino sobre `carteira.services`: ela valida e converte
argumentos, e delega. **Nenhuma tool escreve no banco por conta própria**: o
serviço de domínio é o mesmo que as telas do portal usam, senão teríamos duas
regras de negócio divergindo em silêncio.

Todas declaram `additionalProperties: False`. Já o `strict: True`, que faz a
API garantir que os argumentos batem com o schema, fica só nas ferramentas de
ESCRITA.

O motivo é um limite real da plataforma: o orçamento de complexidade do
`strict` é agregado sobre TODAS as tools da requisição, e com as oito o
servidor responde 400 "Schema is too complex." (medido em 14/09/2026: seis
passam, oito não). Como o orçamento é escasso, ele é gasto onde um argumento
inválido gravaria dinheiro errado no banco; numa consulta, o pior caso é uma
leitura ruim que o modelo refaz.
"""

from __future__ import annotations

from datetime import date

from carteira import services
from carteira.models import TipoTransacao

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_TIPOS = [TipoTransacao.DESPESA, TipoTransacao.RECEITA]

TOOLS = [
    {
        "name": "registrar_transacao",
        "description": (
            "Registra um gasto ou um ganho. Use sempre que a pessoa relatar que gastou, "
            "pagou, comprou, recebeu ou ganhou algum valor. Se a data não for dita, "
            "assuma hoje. Compra parcelada vira uma parcela por mês, passe o total em "
            "'valor' e a quantidade em 'parcelas'."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "valor": {"type": "number", "description": "Valor absoluto, sempre positivo."},
                "descricao": {
                    "type": "string",
                    "description": "Descrição curta, como 'Uber' ou 'Mercado Pão de Açúcar'.",
                },
                "tipo": {"type": "string", "enum": _TIPOS},
                "categoria": {
                    "type": "string",
                    "description": "Nome da categoria. Prefira uma das existentes no contexto.",
                },
                "conta": {
                    "type": "string",
                    "description": "Conta ou cartão usado. Omita se a pessoa não disse.",
                },
                "data": {"type": "string", "description": "AAAA-MM-DD. Omita para hoje."},
                "pago": {"type": "boolean", "description": "False se ainda vai pagar."},
                "parcelas": {
                    "type": "integer",
                    "description": (
                        "Número de parcelas quando a compra foi parcelada ('300 em 3x'). "
                        "Informe o valor TOTAL da compra em 'valor'; a divisão é feita "
                        "aqui. Omita ou 1 para à vista."
                    ),
                },
                "compartilhada": {
                    "type": "boolean",
                    "description": (
                        "False (padrão) para o gasto ficar só com quem lançou. True só "
                        "quando a pessoa disser que é da casa: 'põe no nosso', 'isso é "
                        "nosso', 'conta da casa'."
                    ),
                },
            },
            # Só o que a pessoa de fato informa. Exigir os oito obrigava o
            # modelo a inventar um valor para cada campo que ela não disse,
            # ver o comentário de `editar_transacao`, onde isso quebrou.
            "required": ["valor", "descricao", "tipo", "categoria"],
            "additionalProperties": False,
        },
    },
    {
        "name": "editar_transacao",
        "description": (
            "Corrige um lançamento já registrado, identificado pelo código de 5 caracteres."
        ),
        "strict": True,
        "input_schema": {
            # Só `codigo` é obrigatório: informe apenas os campos a mudar.
            #
            # Antes os seis eram obrigatórios, com "vazio = não alterar" como
            # convenção. Isso quebrou em produção: para mudar só o valor, o
            # modelo tinha de emitir string vazia para os outros quatro, não
            # conseguiu, e ou entrou em laço cuspindo sintaxe corrompida nos
            # parâmetros até estourar o teto de iterações, ou desistiu de
            # chamar a ferramenta. Campo opcional sob `strict` é aceito pela
            # API e resolve, o que ela não aceita é a união `["string",
            # "null"]`, que estourava o orçamento de complexidade com 400
            # "Schema is too complex."
            "type": "object",
            "properties": {
                "codigo": {"type": "string"},
                "valor": {"type": "number"},
                "descricao": {"type": "string"},
                "categoria": {"type": "string"},
                "conta": {"type": "string"},
                "data": {"type": "string", "description": "AAAA-MM-DD."},
            },
            "required": ["codigo"],
            "additionalProperties": False,
        },
    },
    {
        "name": "excluir_transacao",
        "description": "Apaga um lançamento pelo código de 5 caracteres.",
        # A única tool de escrita sem `strict`, e a escolha foi medida: o
        # orçamento de complexidade é agregado sobre TODAS as tools, e o campo
        # `parcelas` novo em `registrar_transacao` não cabia junto com cinco
        # tools strict (400 "Schema is too complex.").
        #
        # Esta é a que menos perde. O único argumento é uma string, e um valor
        # torto cai no "não achei nenhum lançamento com esse código" que já
        # existe. Contra o risco real (apagar o lançamento ERRADO) o `strict`
        # nunca protegeu: ele valida o formato, não a semântica.
        "input_schema": {
            "type": "object",
            "properties": {"codigo": {"type": "string"}},
            "required": ["codigo"],
            "additionalProperties": False,
        },
    },
    {
        "name": "listar_transacoes",
        "description": (
            "Lista lançamentos individuais, do mais recente para o mais antigo. "
            "Use SEMPRE que a pessoa se referir a um lançamento pelo que ele é em vez "
            "do código ('o almoço', 'aquele mercado de ontem', 'o último') para achar "
            "qual é antes de corrigir ou apagar. Também serve para 'o que eu lancei "
            "hoje?'."
        ),
        # Sem `strict`, como as outras tools de leitura. Não é preferência: a
        # sexta tool strict do conjunto estoura o orçamento de complexidade da
        # API com 400 "Schema is too complex.", medido. E aqui custa pouco:
        # argumento torto numa consulta traz menos linhas, enquanto numa tool
        # de escrita gravaria errado.
        "input_schema": {
            "type": "object",
            "properties": {
                "busca": {
                    "type": "string",
                    "description": "Filtra pela descrição. Omita para trazer os últimos.",
                },
                "inicio": {"type": "string", "description": "AAAA-MM-DD."},
                "fim": {"type": "string", "description": "AAAA-MM-DD."},
                "limite": {"type": "integer", "description": "Quantos trazer; padrão 10."},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "consultar_periodo",
        "description": (
            "Resumo financeiro de um período: quanto entrou, quanto saiu, por categoria, "
            "e o split entre gastos fixos e variáveis. Use para perguntas como 'quanto "
            "gastei com mercado esse mês?' ou 'como foi meu mês?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inicio": {"type": "string", "description": "AAAA-MM-DD."},
                "fim": {"type": "string", "description": "AAAA-MM-DD."},
                "categoria": {
                    "type": "string",
                    "description": "Restringe a uma categoria. Omita para todas.",
                },
            },
            "required": ["inicio", "fim"],
            "additionalProperties": False,
        },
    },
    {
        "name": "consultar_planejamento",
        "description": (
            "Saldo previsto de fechamento do mês: o que já aconteceu mais o que ainda "
            "vem dos recorrentes. Use para 'quanto sobra esse mês?', 'dá pra comprar X?' "
            "e 'posso parcelar?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "consultar_limites",
        "description": (
            "Situação de todos os limites de gasto: quanto já foi consumido de cada um."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "criar_limite",
        "description": (
            "Cria um teto de gasto. Sem categoria, é um teto geral do mês. Com 'dias' "
            "preenchido, vira um limite temporário e avulso, que não mexe no orçamento "
            "padrão (ex.: 'R$ 200 pra presente essa semana')."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "valor": {"type": "number"},
                "categoria": {"type": "string", "description": "Omita para teto geral."},
                "rotulo": {"type": "string", "description": "Nome, se for um limite avulso."},
                "dias": {
                    "type": "integer",
                    "description": "Duração em dias se for temporário; omita para mensal.",
                },
            },
            "required": ["valor"],
            "additionalProperties": False,
        },
    },
    {
        "name": "criar_recorrente",
        "description": (
            "Cadastra um ganho ou despesa que se repete todo mês (salário, aluguel, "
            "assinatura). Não lança nada agora: passa a ser considerado na projeção."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "descricao": {"type": "string"},
                "valor": {"type": "number"},
                # Sem `minimum`/`maximum`: sob `strict: True` a API recusa
                # esses validadores em `integer` (400: "For 'integer' type,
                # properties maximum, minimum are not supported"). A faixa fica
                # na descrição, e quem de fato valida é criar_recorrente, que
                # levanta ErroDeDominio e vira tool_result de erro.
                "dia_do_mes": {
                    "type": "integer",
                    "description": "Dia do vencimento, de 1 a 31.",
                },
                "tipo": {"type": "string", "enum": _TIPOS},
                "categoria": {"type": "string"},
                "conta": {"type": "string"},
            },
            "required": ["descricao", "valor", "dia_do_mes", "tipo"],
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def _data(texto: str | None, padrao: date | None = None) -> date | None:
    if not texto:
        return padrao
    try:
        return date.fromisoformat(texto)
    except ValueError as exc:
        raise services.ErroDeDominio(f"Data inválida: “{texto}”.") from exc


def _dinheiro(valor) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def executar(nome: str, argumentos: dict, contexto) -> str:
    """Roda uma tool e devolve o resultado como texto para a Claude.

    Texto e não JSON: o modelo lê melhor, e o resultado volta para ele redigir
    a resposta final ao usuário, não é consumido por código.
    """
    espaco = contexto.espaco
    manipulador = _MANIPULADORES.get(nome)
    if manipulador is None:
        return f"ERRO: ferramenta desconhecida “{nome}”."

    try:
        return manipulador(argumentos, contexto, espaco)
    except services.ErroDeDominio as exc:
        # Devolvido como resultado, não como exceção: a Claude precisa ler o
        # motivo para explicar à pessoa ou tentar de outro jeito.
        return f"ERRO: {exc}"


def _registrar(args, contexto, espaco):
    transacao = services.registrar_transacao(
        espaco=espaco,
        valor=args["valor"],
        descricao=args["descricao"],
        tipo=args.get("tipo") or TipoTransacao.DESPESA,
        categoria=args.get("categoria") or None,
        conta=args.get("conta") or None,
        data_lancamento=_data(args.get("data"), contexto.hoje),
        pago=args.get("pago", True),
        origem=contexto.origem,
        autor=contexto.usuario,
        compartilhada=args.get("compartilhada", False),
        parcelas=args.get("parcelas") or 1,
    )
    categoria = transacao.categoria.nome if transacao.categoria else "sem categoria"
    # Dito assim de propósito: se a pessoa nomeou uma conta que não existe,
    # `achar_conta` devolve None em silêncio, e sem esta pista o agente
    # confirmaria "no cartão" um lançamento que ficou sem conta nenhuma.
    pedida = (args.get("conta") or "").strip()
    if transacao.conta:
        conta = transacao.conta.nome
    elif pedida:
        conta = f"NAO_ENCONTRADA (a pessoa disse “{pedida}”; avise que não existe essa conta)"
    else:
        conta = "sem conta"
    quem_ve = "todo o espaço" if transacao.compartilhada else "só quem lançou"
    linha = (
        f"Registrado. código={transacao.codigo} valor={_dinheiro(transacao.valor)} "
        f"descricao={transacao.descricao} categoria={categoria} conta={conta} "
        f"data={transacao.data:%d/%m/%Y} tipo={transacao.get_tipo_display()} "
        f"quem_ve={quem_ve}"
    )
    if transacao.parcelada:
        irmas = transacao.espaco.transacoes.filter(grupo_parcela=transacao.grupo_parcela).order_by(
            "parcela"
        )
        linha += (
            f" parcelas={transacao.total_parcelas}"
            f" valor_da_parcela={_dinheiro(transacao.valor)}"
            f" total_da_compra={_dinheiro(sum(t.valor for t in irmas))}"
            f" vencimentos={', '.join(f'{t.data:%d/%m}' for t in irmas)}"
        )
    # Quando dividiu, a parte de quem falou é o número que interessa a ela.
    minha = transacao.rateios.filter(pessoa=contexto.usuario).first()
    if minha is not None and minha.valor != transacao.valor:
        linha += f" sua_parte={_dinheiro(minha.valor)}"
    return linha


def _editar(args, contexto, espaco):
    # Campo ausente vira None, que é o que o serviço espera como "não mexe".
    # O `or None` continua cobrindo o 0/"" que um modelo antigo poderia mandar.
    transacao = services.editar_transacao(
        espaco=espaco,
        codigo=args["codigo"],
        usuario=contexto.usuario,
        valor=args.get("valor") or None,
        descricao=args.get("descricao") or None,
        categoria=args.get("categoria") or None,
        conta=args.get("conta") or None,
        data_lancamento=_data(args.get("data")),
    )
    return (
        f"Atualizado. código={transacao.codigo} valor={_dinheiro(transacao.valor)} "
        f"descricao={transacao.descricao} data={transacao.data:%d/%m/%Y}"
    )


def _excluir(args, contexto, espaco):
    resumo = services.excluir_transacao(
        espaco=espaco, codigo=args["codigo"], usuario=contexto.usuario
    )
    return f"Excluído. código={resumo['codigo']} descricao={resumo['descricao']}"


def _listar_transacoes(args, contexto, espaco):
    """Lançamentos individuais, com o código, para o modelo resolver sozinho.

    É o que permite a conversa não ter código nenhum: a pessoa diz "o almoço",
    o modelo acha aqui e edita pelo código sem nunca mostrá-lo. Sem esta tool,
    só dava para corrigir o que ainda estivesse na janela curta de histórico.

    O recorte de visibilidade é o mesmo de todo o resto (`visiveis_para`) e
    não é opcional: sem ele, listar entregaria o gasto pessoal de quem divide
    o espaço.
    """
    from carteira.models import Transacao

    consulta = services.visiveis_para(
        Transacao.objects.filter(espaco=espaco).select_related("categoria", "conta"),
        contexto.usuario,
    )

    busca = (args.get("busca") or "").strip()
    if busca:
        consulta = consulta.filter(descricao__icontains=busca)
    inicio = _data(args.get("inicio"))
    if inicio:
        consulta = consulta.filter(data__gte=inicio)
    fim = _data(args.get("fim"))
    if fim:
        consulta = consulta.filter(data__lte=fim)

    # Teto rígido: o resultado vira contexto do modelo, e uma lista sem limite
    # queimaria a quota da pessoa num "lista tudo".
    limite = min(max(int(args.get("limite") or 10), 1), 30)
    achados = list(consulta.order_by("-data", "-id")[:limite])

    if not achados:
        return "Nenhum lançamento encontrado com esses filtros."

    linhas = []
    for t in achados:
        categoria = t.categoria.nome if t.categoria else "sem categoria"
        linhas.append(
            f"codigo={t.codigo} data={t.data:%d/%m/%Y} descricao={t.rotulo} "
            f"valor={_dinheiro(t.valor)} categoria={categoria} "
            f"tipo={t.get_tipo_display()} pago={'sim' if t.pago else 'nao'}"
        )
    return "\n".join(linhas)


def _consultar_periodo(args, contexto, espaco):
    inicio = _data(args.get("inicio")) or contexto.hoje.replace(day=1)
    fim = _data(args.get("fim")) or contexto.hoje
    nome_categoria = args.get("categoria") or ""

    if nome_categoria:
        categoria = services.achar_categoria(espaco, nome_categoria)
        total = services.total_gasto(
            espaco, inicio, fim, categoria=categoria, usuario=contexto.usuario
        )
        return f"De {inicio:%d/%m} a {fim:%d/%m}, gasto em {categoria.nome}: {_dinheiro(total)}."

    resumo = services.resumo_periodo(espaco, inicio, fim, usuario=contexto.usuario)
    linhas = [
        f"Período {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}",
        f"entradas={_dinheiro(resumo.receitas)} saidas={_dinheiro(resumo.despesas)} "
        f"saldo={_dinheiro(resumo.saldo)}",
        f"fixos={_dinheiro(resumo.fixas)} variaveis={_dinheiro(resumo.variaveis)}",
    ]
    if resumo.por_categoria:
        topo = ", ".join(f"{nome}: {_dinheiro(valor)}" for nome, valor in resumo.por_categoria[:8])
        linhas.append(f"por categoria: {topo}")
    else:
        linhas.append("Nenhuma despesa no período.")
    return "\n".join(linhas)


def _consultar_planejamento(args, contexto, espaco):
    # Materializa antes de somar: sem isto, quem cadastrou um recorrente agora
    # veria a projeção sem ele.
    services.projetar_recorrentes(espaco, contexto.hoje)
    s = services.saldo_previsto(espaco, contexto.hoje, usuario=contexto.usuario)
    return (
        f"Mês de {s['inicio']:%m/%Y}. "
        f"já entrou={_dinheiro(s['receitas_realizadas'])} "
        f"já saiu={_dinheiro(s['despesas_realizadas'])} "
        f"ainda a receber={_dinheiro(s['a_receber'])} "
        f"ainda a pagar={_dinheiro(s['a_pagar'])} "
        f"saldo hoje={_dinheiro(s['saldo_realizado'])} "
        f"saldo previsto no fim do mês={_dinheiro(s['saldo_previsto'])}"
    )


def _consultar_limites(args, contexto, espaco):
    from carteira.models import Limite

    limites = Limite.objects.filter(espaco=espaco, ativo=True).select_related("categoria")
    if not limites:
        return "Nenhum limite cadastrado."

    linhas = []
    for limite in limites:
        c = services.consumo_do_limite(limite, contexto.hoje, usuario=contexto.usuario)
        alvo = limite.categoria.nome if limite.categoria else (limite.rotulo or "geral")
        marca = " ESTOUROU" if c["estourado"] else ""
        linhas.append(
            f"{alvo}: {_dinheiro(c['gasto'])} de {_dinheiro(limite.valor)} "
            f"({c['percentual']}%), restam {_dinheiro(c['restante'])}{marca}"
        )
    return "\n".join(linhas)


def _criar_limite(args, contexto, espaco):
    limite = services.criar_limite(
        espaco=espaco,
        valor=args["valor"],
        categoria=args.get("categoria") or None,
        rotulo=args.get("rotulo") or "",
        dias=args.get("dias") or None,
    )
    alvo = limite.categoria.nome if limite.categoria else (limite.rotulo or "geral")
    prazo = f" até {limite.fim:%d/%m}" if limite.temporario else " por mês"
    return f"Limite criado: {alvo} {_dinheiro(limite.valor)}{prazo}."


def _criar_recorrente(args, contexto, espaco):
    regra = services.criar_recorrente(
        espaco=espaco,
        descricao=args["descricao"],
        valor=args["valor"],
        dia_do_mes=args["dia_do_mes"],
        tipo=args.get("tipo") or TipoTransacao.DESPESA,
        categoria=args.get("categoria") or None,
        conta=args.get("conta") or None,
        autor=contexto.usuario,
    )
    services.projetar_recorrentes(espaco, contexto.hoje)
    return (
        f"Recorrente criado: {regra.descricao} {_dinheiro(regra.valor)} "
        f"todo dia {regra.dia_do_mes}."
    )


_MANIPULADORES = {
    "registrar_transacao": _registrar,
    "editar_transacao": _editar,
    "excluir_transacao": _excluir,
    "listar_transacoes": _listar_transacoes,
    "consultar_periodo": _consultar_periodo,
    "consultar_planejamento": _consultar_planejamento,
    "consultar_limites": _consultar_limites,
    "criar_limite": _criar_limite,
    "criar_recorrente": _criar_recorrente,
}

# Tools que só leem. Usado para escolher o `effort`: consultar exige
# julgamento, registrar é mecânico.
SOMENTE_LEITURA = frozenset(
    {
        "listar_transacoes",
        "consultar_periodo",
        "consultar_planejamento",
        "consultar_limites",
    }
)

__all__ = ["SOMENTE_LEITURA", "TOOLS", "executar"]
