# Dracma

Assistente financeira pessoal com IA que vive no **Telegram**, com portal web
complementar. Você conta um gasto por texto, áudio, foto de comprovante ou PDF;
ela entende, categoriza, registra — e avisa **antes** de o limite estourar.

> Projeto de portfólio. Não é consultoria financeira e não se conecta a bancos.

---

## O que ele faz

**Registro sem atrito.** "uber 34 reais", um áudio no trânsito, o print do PIX
ou o PDF do extrato. A Claude interpreta, escolhe a categoria e confirma numa
linha. Para corrigir depois, fala-se do lançamento como ele é — "muda o valor
do almoço para 30" — e a assistente resolve qual é (`listar_transacoes`). Cada
lançamento tem um código curto (`0DFPK`), mas ele é identificador interno e
aparece no portal, não na conversa: ninguém decora cinco caracteres aleatórios.

**Orçamento que se cuida sozinho.** Limites por categoria, teto geral do mês, e
limites temporários avulsos ("R$ 200 pra presente essa semana"). Ao chegar em
80% e ao estourar, a Dracma manda mensagem — antes da fatura fechar.

**Planejamento.** Ganhos e despesas recorrentes viram projeção: saldo previsto
de fechamento do mês, o que ainda entra e o que ainda sai.

**Colaboração, com privacidade.** Toda transação pertence a um *espaço*, não a
uma pessoa: casal, família ou time acompanham o mesmo mês, cada um lançando do
próprio Telegram. Mas dividir a conta da casa não é abrir o extrato inteiro —
cada lançamento é **compartilhado** ou **só eu**, e o que é pessoal some da
visão dos outros, do CSV, dos alertas e do que a assistente responde.

**Rateio e acerto.** O que é da casa é dividido: igual, por porcentagem ou por
valor. O espaço tem uma divisão padrão — meio a meio, ou 60/40 porque as rendas
são diferentes — e cada gasto pode sair dela sem alterá-la. Nos totais de cada
um entra a **fatia**, não o valor cheio. O painel fecha o mês dizendo quem deve
quanto a quem, e o pagamento é registrado com um botão: pagou tudo, o mês zera;
pagou parte, o que sobra continua aparecendo.

**Portal.** Uma página só: saldo do mês, para onde o dinheiro foi, limites,
recorrentes, lançamentos e a conversa com a assistente — tudo no mesmo painel,
com as edições em diálogo ou na própria linha. Exportação em CSV.

**Onboarding.** A tela *Conectar Telegram* resolve os três pontos de partida:
QR para quem está no desktop e precisa levar o link ao celular, deep link
`t.me/<bot>?start=<token>` para quem já está no telefone — um toque, o token
chega sozinho no `/start` e não se digita nada — e o código de 6 dígitos para
quem achou o bot pela busca, com a opção de receber tudo por e-mail. Depois de conectar, a própria
conversa ensina: três mensagens espaçadas mostram os formatos aceitos, sugerem
o primeiro limite e apontam o portal.

---

## Como funciona

```
Telegram → nginx → view (confere o segredo, grava, 200 OK em milissegundos)
                        ↓ Celery
              baixa mídia → transcreve (áudio) / lê (imagem, PDF)
                        ↓
              agente Claude: contexto do espaço + tool use
                        ↓
              serviço de domínio grava → responde pelo canal
```

A view do webhook **nunca** chama a IA. O Telegram re-tenta quando a resposta
demora e, com falhas repetidas, vai espaçando as entregas até o bot ficar mudo
— então ela valida, persiste o payload cru e entrega o resto ao Celery.

### As decisões que mais moldaram o código

**1. O canal é uma interface, e foi ela que pagou a migração.** `CanalMensagem`
tem três implementações: `telegram` fala com a Bot API, `console` desenha no
portal, `fake` guarda em memória para os testes. O app nasceu no WhatsApp e
trocou de plataforma sem que `carteira` nem `ai` mudassem uma linha — o que a
troca tocou foi o transporte, e é exatamente isso que a interface delimita.

**2. Trocar de plataforma apagou uma regra de domínio inteira.** A Meta só
permitia resposta em formato livre dentro de 24h desde a última mensagem *do
usuário*; fora disso, só template aprovado. Um alerta proativo, portanto, não
podia simplesmente ser enviado: havia uma janela a consultar, um template a
escolher e o caso de adiar. No Telegram nada disso existe — depois do `/start`,
o bot escreve quando quiser. `zap/janela.py` virou `bot/envio.py` e encolheu
para o que sobrou de real: mandar, registrar, e parar de insistir com quem
bloqueou o bot (403, marcado em `ContaTelegram.bloqueado_em` e limpo sozinho se
a pessoa desbloquear).

**3. A visibilidade é uma regra só.** `services.visiveis_para` responde
"o que esta pessoa pode ver": ou o lançamento é compartilhado, ou é dela. Está
num lugar só porque espalhada o primeiro relatório novo esqueceria dela — e o
erro aqui não é tela quebrada, é vazamento. Limites são a exceção deliberada:
contam **só o que é do espaço**, porque o alerta vai para todo mundo e um
percentual calculado com gasto pessoal entregaria esse gasto.

**4. Caixa e custo são números diferentes.** A conta de luz de R$ 310 paga da
conta conjunta tira R$ 310 dela e custa R$ 155 a cada uma. Saldo de conta usa o
valor cheio; totais, limites e relatórios usam a fatia. Confundir os dois faria
o extrato não bater com o banco ou o custo aparecer dobrado.

**5. Existe um ponto único de escrita.** A resposta da Claude nunca escreve no
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
| `carteira` | domínio financeiro, rateio e o painel. Não conhece Telegram nem IA. |
| `bot` | transporte: webhook, canais, mídia, envio, onboarding e pareamento |
| `ai` | cliente Claude, tools, loop do agente, transcrição |
| `accounts` | usuário, espaço, modo visitante, quota de IA |
| `legal` | termos e privacidade versionados, com aceite obrigatório |

A dependência é de mão única: `bot` e `ai` chamam `carteira`; `carteira` não
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

Com `TELEGRAM_ENABLED=False` (o padrão), o webhook responde 404 e o canal cai
para o console: dá para usar o produto inteiro pelo painel, sem túnel HTTPS e
sem bot registrado.

Para áudio é preciso o `ffmpeg` no sistema. O modelo do faster-whisper é
baixado na primeira transcrição, para `WHISPER_CACHE_DIR` — que fica **fora** da
árvore do projeto, senão cada deploy limpo rebaixaria ~500 MB.

### Testes

```bash
./venv/bin/python -m pytest
```

Sem chamar a API da Anthropic nem a do Telegram: `ai/fakes.py` tem um cliente
Claude falso e `bot/canais/fake.py` um canal que acumula em memória.

Os módulos que mais valem a leitura, porque um erro neles não dá tela quebrada
e sim vazamento ou dinheiro errado:

| Arquivo | O que trava |
|---|---|
| `carteira/tests_compartilhamento.py` | quem vê o quê, em cada caminho que consulta dinheiro |
| `carteira/tests_rateio.py` | a divisão fecha ao centavo, e o acerto abate o mês certo |
| `bot/tests_webhook.py` | autenticidade, idempotência e o 200 rápido |
| `bot/tests_envio.py` | entrega, bloqueio do bot e a volta dele |

---

## Ligando o Telegram

1. No Telegram, fale com o [@BotFather](https://t.me/BotFather): `/newbot`,
   escolha o nome e o `@username`. Ele devolve o **token**, no formato
   `<id do bot>:<segredo>`. É a credencial inteira do bot — quem a tem lê e
   escreve toda conversa.
2. Gere o segredo do webhook:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. Preencha no `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` (sem o
   `@`), `TELEGRAM_WEBHOOK_SECRET` e `TELEGRAM_ENABLED=True`.
4. Registre o webhook (o `secret_token` é o que autentica cada POST):

   ```bash
   curl -sS "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -d "url=https://SEU_DOMINIO/bot/webhook/" \
     -d "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
     -d "allowed_updates=[\"message\"]"
   ```

   Confira com `getWebhookInfo`: `pending_update_count` alto ou
   `last_error_message` preenchido significa que o Telegram não está
   conseguindo entregar.
5. No portal, abra **Conectar Telegram** e toque no botão — ou mande as
   instruções para o seu e-mail e abra pelo celular.

**Autenticidade do webhook.** A Bot API não assina o corpo como a Graph API
fazia com o `X-Hub-Signature-256`; o mecanismo oficial é o `secret_token`, que
o Telegram repete em `X-Telegram-Bot-Api-Secret-Token` a cada POST. Sem ele
configurado a view recusa tudo, de propósito: aceitar sem conferir deixaria a
URL aberta para quem a descobrisse injetar transação em conta alheia.

**Mídia.** O download passa de 20 MB só com um *Bot API Server* local, apontado
por `TELEGRAM_API_BASE`. Contra a `api.telegram.org` o teto é da plataforma e
aumentar `TELEGRAM_MAX_MIDIA_BYTES` não adianta.

**Custo.** A Bot API é gratuita e não há limite de mensagens por mês, nem
janela de atendimento, nem template a aprovar. O limite prático é de vazão
(~30 mensagens por segundo, 1 por segundo na mesma conversa), muito acima do
que um projeto pessoal usa.

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

## Provisionamento

O que **já está pronto** no repositório: unidades systemd, config do nginx,
`cd-deploy.sh`, workflows de CI e CD, `requirements.lock` com hashes,
`.env.example` e as migrações. `check --deploy` passa limpo e não há migração
pendente.

O que **precisa ser feito no servidor**, uma vez:

O que precisa de root está em **`deploy/provisionar.sh`** — serviços, nginx e
certificado, idempotente. O resto vem antes:

```bash
# 1. Diretórios (o /var/www é do root; o venv e o modelo do whisper ficam fora
#    da árvore do git para o deploy não rebaixar 500 MB a cada vez)
sudo mkdir -p /var/www/dracma /var/lib/dracma/whisper
sudo chown rod:www-data /var/www/dracma /var/lib/dracma/whisper

# 2. Banco
sudo -u postgres createuser dracma --pwprompt
sudo -u postgres createdb dracma --owner=dracma

# 3. Código e venv
git clone https://github.com/rigst/dracma.git /var/www/dracma
cd /var/www/dracma
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env   # e preencha (ver abaixo)

# 4. Primeira carga
./venv/bin/python manage.py migrate
./venv/bin/python manage.py importar_documentos_legais --publicar
./venv/bin/python manage.py createsuperuser
./venv/bin/python manage.py collectstatic --noinput

# 5. Serviços, nginx e certificado, de uma vez
sudo /var/www/dracma/deploy/provisionar.sh

# 6. Seu usuário (interativo)
DJANGO_SETTINGS_MODULE=config.settings.production \
  ./venv/bin/python manage.py createsuperuser
```

O `provisionar.sh` sobe o nginx **só com HTTP** antes de pedir o certificado:
a config definitiva referencia o `fullchain.pem`, e instalada antes de ele
existir o `nginx -t` falha e o nginx nem recarrega. Emitido o certificado, ele
troca pela definitiva e testa o domínio de ponta a ponta.

E no GitHub: criar `rigst/dracma`, adicionar os secrets `CODECOV_TOKEN`,
`SONAR_TOKEN` e `CD_SSH_KEY`, e a chave do usuário `deploy` no servidor com
`command=` forçado apontando para `deploy/cd-deploy.sh` (RUNBOOK §7 do
`rigst/ci`).

### Recursos alocados

| Recurso | Valor | Conferido |
|---|---|---|
| Porta do gunicorn | **8015** | livre (8000–8014 ocupadas) |
| Redis | **DB 4** cache, **5** sessões, **6** broker | livres (0–3, 9, 10 em uso) |
| Sistema | `ffmpeg` e `ffprobe` | presentes |

### Três armadilhas deste deploy

**E-mail cai no console por padrão.** `EMAIL_BACKEND` não configurado escreve a
mensagem no log e não entrega nada — instruções de conexão e recuperação de
senha sumiriam em silêncio. O `.env.example` já vem com o backend SMTP; falta
preencher host, usuário e senha.

**O deploy publica os documentos legais, e falha se eles divergirem.** Sem a
publicação, o primeiro deploy sobe com `/termos/` e `/privacidade/` em 404. E
se o markdown de uma versão já publicada mudar, o `cd-deploy.sh` para: o
repositório e o texto que as pessoas aceitaram estariam discordando. O conserto
é criar uma versão nova em `legal/documentos/`, não editar a antiga.

**O CI sobe com `soft-fail`.** `ci.yml` começa tolerando falha em
`mypy,bandit,pip-audit`, na ordem do RUNBOOK §4. Zerar isso é a última etapa,
depois que o resto estiver verde.

### Antes de ligar o Telegram

O app funciona sem ele: com `TELEGRAM_ENABLED=False` o webhook responde 404 e a
assistente atende pelo console do painel. Ligar depois é preencher as
credenciais e apontar o webhook — nada no domínio muda.

## Licença

AGPL-3.0
