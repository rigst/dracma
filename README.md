# Centavo

Assistente financeira pessoal com IA que vive no **WhatsApp**, com portal web
complementar. Você conta um gasto por texto, áudio, foto de comprovante ou PDF;
ela entende, categoriza, registra — e avisa **antes** de o limite estourar.

> Projeto de portfólio. Não é consultoria financeira e não se conecta a bancos.

---

## O que ele faz

**Registro sem atrito.** "uber 34 reais", um áudio no trânsito, o print do PIX
ou o PDF do extrato. A Claude interpreta, escolhe a categoria e devolve um
código curto (`0DFPK`) para corrigir depois.

**Orçamento que se cuida sozinho.** Limites por categoria, teto geral do mês, e
limites temporários avulsos ("R$ 200 pra presente essa semana"). Ao chegar em
80% e ao estourar, a Centavo manda mensagem — antes da fatura fechar.

**Planejamento.** Ganhos e despesas recorrentes viram projeção: saldo previsto
de fechamento do mês, o que ainda entra e o que ainda sai.

**Colaboração.** Toda transação pertence a um *espaço*, não a uma pessoa. Casal,
família ou time acompanham o mesmo mês, cada um lançando do próprio WhatsApp.

**Portal.** Painel, transações com filtro, relatórios com gráficos, exportação
em CSV — e um **console** onde dá para conversar com a mesma assistente pelo
navegador.

---

## Como funciona

```
WhatsApp → nginx → view (valida HMAC, grava, 200 OK em milissegundos)
                        ↓ Celery
              baixa mídia → transcreve (áudio) / lê (imagem, PDF)
                        ↓
              agente Claude: contexto do espaço + tool use
                        ↓
              serviço de domínio grava → responde pelo canal
```

A view do webhook **nunca** chama a IA. A Meta re-tenta quando a resposta
demora e, com falhas repetidas, desabilita a subscrição — então ela valida,
persiste o payload cru e entrega o resto ao Celery.

### As três decisões que mais moldaram o código

**1. A janela de 24 horas é uma regra de domínio.** A Meta só permite resposta
em formato livre dentro de 24h desde a última mensagem *do usuário*; fora disso,
só template aprovado. Ou seja, **um alerta proativo não pode simplesmente ser
enviado**. A decisão mora em `zap/janela.py`, num lugar só — espalhada como um
`if` em cada ponto de envio, a primeira task nova esqueceria dela e as
mensagens passariam a falhar em silêncio.

**2. O canal é uma interface.** `CanalMensagem` tem três implementações:
`cloud_api` fala com a Meta, `console` desenha no portal, `fake` guarda em
memória para os testes. É isso que permite construir e demonstrar o agente
inteiro sem tocar na Meta — e o que sustenta a demo pública, já que o número de
teste dela só atende 5 destinatários allowlistados.

**3. Existe um ponto único de escrita.** A resposta da Claude nunca escreve no
banco: as tools validam argumentos e delegam para `carteira/services.py`, o
mesmo módulo que as telas do portal usam. Sem isso, seriam duas regras de
negócio divergindo em silêncio.

---

## Stack

Django 6 · PostgreSQL · Celery + Redis · Gunicorn · nginx · Claude (Anthropic) ·
faster-whisper · HTMX

Sem bundler, sem Docker, sem framework de frontend. Os gráficos são SVG gerado
no servidor — funcionam com o JavaScript desligado e herdam os tokens de tema.

### Apps

| App | Responsabilidade |
|---|---|
| `carteira` | domínio financeiro e telas do portal. Não conhece WhatsApp nem IA. |
| `zap` | transporte: webhook, canais, mídia, janela de atendimento, pareamento |
| `ai` | cliente Claude, tools, loop do agente, transcrição |
| `accounts` | usuário, espaço, modo visitante, quota de IA |
| `legal` | termos e privacidade versionados, com aceite obrigatório |

A dependência é de mão única: `zap` e `ai` chamam `carteira`; `carteira` não
importa nenhum dos dois.

---

## Rodando localmente

```bash
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env          # ajuste ANTHROPIC_API_KEY
./venv/bin/python manage.py migrate
./venv/bin/python manage.py importar_documentos_legais --publicar
./venv/bin/python manage.py createsuperuser
./venv/bin/python manage.py runserver
```

Com `WHATSAPP_ENABLED=False` (o padrão), o webhook responde 404 e o canal cai
para o console: dá para usar o produto inteiro em `/zap/console/` sem ngrok e
sem credencial da Meta.

Para áudio é preciso o `ffmpeg` no sistema. O modelo do faster-whisper é
baixado na primeira transcrição, para `WHISPER_CACHE_DIR` — que fica **fora** da
árvore do projeto, senão cada deploy limpo rebaixaria ~500 MB.

### Testes

```bash
./venv/bin/python -m pytest
```

253 testes, sem chamar a API da Anthropic nem a da Meta: `ai/fakes.py` tem um
cliente Claude falso e `zap/canais/fake.py` um canal que acumula em memória.

---

## Ligando o WhatsApp

1. No [Meta for Developers](https://developers.facebook.com/), crie um app do
   tipo *Business* e adicione o produto **WhatsApp**. Ele já vem com um número
   de teste gratuito, sem verificação de negócio, que envia para até **5
   destinatários allowlistados**.
2. Preencha no `.env`: `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`,
   `WHATSAPP_APP_SECRET` e um `WHATSAPP_VERIFY_TOKEN` escolhido por você.
3. Aponte o webhook para `https://SEU_DOMINIO/zap/webhook/`, repetindo o mesmo
   verify token, e assine o campo `messages`.
4. `WHATSAPP_ENABLED=True`.
5. No portal, gere o código de pareamento e mande-o pelo WhatsApp para vincular
   o número.

**Templates.** Os alertas fora da janela de 24h exigem template *utility*
aprovado no painel. Sem `WHATSAPP_TEMPLATE_LIMITE` e
`WHATSAPP_TEMPLATE_VENCIMENTO` configurados, esses alertas são registrados como
adiados e saem na próxima vez que a pessoa falar — insistir numa entrega que a
Meta recusa aproxima o número de ser bloqueado.

**Custo.** O acesso à plataforma é gratuito; paga-se por mensagem. Desde
**1º/10/2026** as *service messages* (respostas dentro da janela de 24h) são
cobradas **após 1.000 grátis por mês por número** — volume de projeto pessoal
não encosta nisso. É preciso ter método de pagamento cadastrado para que
continuem sendo entregues.

---

## Custo da IA

Um modelo só (`claude-opus-5`). As alavancas de custo são outras:

- **Prompt caching** no prefixo estável do system (instruções + tools). A data
  de hoje e as categorias do espaço vêm **depois** do breakpoint — no último
  bloco, o cache seria invalidado toda meia-noite e a cada espaço diferente.
- **`effort`** baixo para registrar (trabalho mecânico) e alto depois de uma
  ferramenta de consulta, quando a pergunta virou analítica.
- **Quota mensal por usuário**, com balde próprio e menor para visitantes: a
  demo é pública e anônima.

Cada chamada grava `usage` em `ConsumoIA`, com o custo em dólar calculado
separando cache de escrita (~1,25x) de cache de leitura (~0,1x).

---

## Licença

AGPL-3.0
