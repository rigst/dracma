# Dracma

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
80% e ao estourar, a Dracma manda mensagem — antes da fatura fechar.

**Planejamento.** Ganhos e despesas recorrentes viram projeção: saldo previsto
de fechamento do mês, o que ainda entra e o que ainda sai.

**Colaboração, com privacidade.** Toda transação pertence a um *espaço*, não a
uma pessoa: casal, família ou time acompanham o mesmo mês, cada um lançando do
próprio WhatsApp. Mas dividir a conta da casa não é abrir o extrato inteiro —
cada lançamento é **compartilhado** ou **só eu**, e o que é pessoal some da
visão dos outros, do CSV, dos alertas e do que a assistente responde.

**Portal.** Uma página só: saldo do mês, para onde o dinheiro foi, limites,
recorrentes, lançamentos e a conversa com a assistente — tudo no mesmo painel,
com as edições em diálogo ou na própria linha. Exportação em CSV.

**Onboarding.** A tela *Conectar WhatsApp* resolve os três pontos de partida:
QR para quem está no desktop e precisa levar o link ao celular, deep link
`wa.me` para quem já está no telefone, e o código de 6 dígitos para digitar à
mão — com a opção de receber tudo por e-mail. Depois de conectar, a própria
conversa ensina: três mensagens espaçadas mostram os formatos aceitos, sugerem
o primeiro limite e apontam o portal.

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

**3. A visibilidade é uma regra só.** `services.visiveis_para` responde
"o que esta pessoa pode ver": ou o lançamento é compartilhado, ou é dela. Está
num lugar só porque espalhada o primeiro relatório novo esqueceria dela — e o
erro aqui não é tela quebrada, é vazamento. Limites são a exceção deliberada:
contam **só o que é do espaço**, porque o alerta vai para todo mundo e um
percentual calculado com gasto pessoal entregaria esse gasto.

**4. Existe um ponto único de escrita.** A resposta da Claude nunca escreve no
banco: as tools validam argumentos e delegam para `carteira/services.py`, o
mesmo módulo que as telas do portal usam. Sem isso, seriam duas regras de
negócio divergindo em silêncio.

---

## Stack

Django 6 · PostgreSQL · Celery + Redis · Gunicorn · nginx · Claude (Anthropic) ·
faster-whisper · HTMX

Sem bundler, sem Docker, sem framework de frontend. Os gráficos, o meandro e a
moeda são SVG — gerados no servidor ou inline — e herdam os tokens de tema.

### O visual

O nome vem da dracma ateniense, e o sistema visual vem da cerâmica ática de
figuras negras: o desenho é a silhueta escura sobre a argila alaranjada, e os
detalhes são **incisos**, riscados até aparecer o barro por baixo.

Daí a decisão que organiza tudo: **a moldura é cerâmica, o conteúdo é mármore**.
O cabeçalho é um friso de vaso — fundo negro, letra em terracota, meandro — e os
dados moram em painéis de mármore. O tema escuro inverte para a vasilha inteira,
que é como a peça realmente é; o claro mantém o mármore. É o que faz o botão de
tema significar alguma coisa em vez de ser enfeite.

A paleta sai dos pigmentos: terracota, ocre, oliva, o *added purple* da
cerâmica, o azul-egeu da policromia dos templos. A tipografia de exibição é a
**Cormorant Garamond**, auto-hospedada (SIL OFL), em versalete com entreletra
larga nos rótulos — a letra gravada, não a de interface.

A assinatura é a moeda: coruja de Atena, ramo de oliveira e a lua crescente, os
três elementos do tetradracma. Ela é o logo, o favicon e o ícone dos estados
vazios.

### Apps

| App | Responsabilidade |
|---|---|
| `carteira` | domínio financeiro e o painel. Não conhece WhatsApp nem IA. |
| `zap` | transporte: webhook, canais, mídia, janela de atendimento, onboarding |
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

256 testes, sem chamar a API da Anthropic nem a da Meta: `ai/fakes.py` tem um
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
5. Preencha também `WHATSAPP_NUMERO` com o número em E.164 sem o `+` (ex.:
   `5511999998888`). Ele é diferente do `PHONE_NUMBER_ID` e é o que monta o
   link `wa.me` e o QR da tela de conexão.
6. No portal, abra **Conectar WhatsApp** e siga os passos — ou mande as
   instruções para o seu e-mail e abra pelo celular.

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

Um modelo só (`claude-sonnet-5`). As alavancas de custo são outras:

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
