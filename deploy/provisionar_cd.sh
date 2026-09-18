#!/usr/bin/env bash
#
# Provisiona a chave SSH do deploy contínuo da Dracma.
#
#   /var/www/dracma/deploy/provisionar_cd.sh
#
# Rode como "rod", SEM sudo: o script usa o `gh` autenticado da sua conta para
# gravar o secret, e chama `sudo` sozinho só nas duas linhas que precisam de
# root (criar o ~deploy/.ssh e acrescentar a linha no authorized_keys).
#
# O que ele faz, na ordem:
#   1. gera um par ed25519 novo, exclusivo deste app;
#   2. grava a privada como o secret CD_SSH_KEY do repositório rigst/dracma;
#   3. acrescenta a pública ao authorized_keys do usuário "deploy", presa a um
#      comando forçado — aquela chave não abre shell, só roda o cd-deploy.sh;
#   4. TESTA a chave de verdade, de fora, antes de destruí-la;
#   5. apaga o par com shred. A privada existe daqui em diante só no cofre do
#      GitHub e a pública só no authorized_keys.
#
# É idempotente: rodar de novo gera uma chave nova, substitui o secret e troca
# a linha antiga no authorized_keys em vez de empilhar uma segunda.
#
# Ver rigst/ci RUNBOOK.md seção 7.3. O que este script NÃO faz é o sudoers do
# passo 2: sem aquele arquivo o deploy falha no fim, no systemctl.
set -euo pipefail

APP=dracma
REPO=rigst/dracma
HOST=dracma.stolben.com
USUARIO_DEPLOY=deploy
COMANDO_FORCADO=/var/www/dracma/deploy/cd-deploy.sh
# Marca própria na linha do authorized_keys, para o script saber qual linha é
# dele quando rodar de novo. O comentário de uma chave ed25519 fica no fim da
# linha, e é por ele que a substituição acha a chave antiga deste app.
COMENTARIO="cd-deploy-$APP"

erro() { echo "ERRO: $*" >&2; exit 1; }
passo() { echo; echo "── $* ──"; }

[[ $EUID -ne 0 ]] || erro "rode como você mesmo, sem sudo: o gh precisa da SUA conta."
command -v gh >/dev/null || erro "gh não encontrado."
command -v ssh-keygen >/dev/null || erro "ssh-keygen não encontrado."
gh auth status >/dev/null 2>&1 || erro "gh não autenticado. Rode: gh auth login"
id "$USUARIO_DEPLOY" >/dev/null 2>&1 \
  || erro "usuário '$USUARIO_DEPLOY' não existe. Ver RUNBOOK.md seção 7.1."
[[ -x "$COMANDO_FORCADO" ]] \
  || erro "$COMANDO_FORCADO não existe ou não é executável."
gh repo view "$REPO" >/dev/null 2>&1 \
  || erro "sem acesso a $REPO com a conta autenticada no gh."

# O par nasce num diretório só seu, 0700, e não em /tmp com nome previsível:
# entre gerar e destruir a privada fica alguns segundos em disco.
TRABALHO="$(mktemp -d "${TMPDIR:-/tmp}/cd-keys-$APP.XXXXXX")"
chmod 700 "$TRABALHO"
CHAVE="$TRABALHO/cd_$APP"
# `shred` no que sobrar, aconteça o que acontecer: o trap cobre também a saída
# por erro no meio, que é justamente quando a privada ficaria esquecida ali.
limpar() { shred -u "$CHAVE" "$CHAVE.pub" 2>/dev/null || true; rm -rf "$TRABALHO"; }
trap limpar EXIT

passo "Gerando o par ed25519"
ssh-keygen -t ed25519 -C "$COMENTARIO" -f "$CHAVE" -N "" -q
echo "  $(ssh-keygen -lf "$CHAVE.pub")"

passo "Gravando a privada como secret CD_SSH_KEY em $REPO"
gh secret set CD_SSH_KEY --repo "$REPO" < "$CHAVE"
echo "  gravado."

passo "Autorizando a pública no $USUARIO_DEPLOY, presa ao comando forçado"
GRUPO_DEPLOY="$(id -gn "$USUARIO_DEPLOY")"
sudo install -d -m 700 -o "$USUARIO_DEPLOY" -g "$GRUPO_DEPLOY" \
  "/home/$USUARIO_DEPLOY/.ssh"
sudo touch "/home/$USUARIO_DEPLOY/.ssh/authorized_keys"

# `restrict` (OpenSSH >= 7.2) equivale a no-port-forwarding, no-X11-forwarding,
# no-agent-forwarding e no-pty numa palavra só. Com o `command=`, o que o runner
# pedir é ignorado: o sshd roda o cd-deploy.sh e passa o pedido original em
# SSH_ORIGINAL_COMMAND, de onde o script lê o SHA.
LINHA="restrict,command=\"$COMANDO_FORCADO\" $(cat "$CHAVE.pub")"

# Substitui a linha deste app se ela já existir. Sem isto, rodar o script duas
# vezes deixaria a chave velha valendo: ela continuaria abrindo o deploy mesmo
# depois de o secret ter sido trocado, e ninguém repararia.
if sudo grep -q " $COMENTARIO\$" "/home/$USUARIO_DEPLOY/.ssh/authorized_keys"; then
  echo "  já havia uma chave deste app: substituindo (a antiga deixa de valer)."
  sudo sed -i "/ $COMENTARIO\$/d" "/home/$USUARIO_DEPLOY/.ssh/authorized_keys"
fi
printf '%s\n' "$LINHA" \
  | sudo tee -a "/home/$USUARIO_DEPLOY/.ssh/authorized_keys" > /dev/null
sudo chown "$USUARIO_DEPLOY:$GRUPO_DEPLOY" \
  "/home/$USUARIO_DEPLOY/.ssh/authorized_keys"
sudo chmod 600 "/home/$USUARIO_DEPLOY/.ssh/authorized_keys"
echo "  autorizada ($(sudo grep -c '' "/home/$USUARIO_DEPLOY/.ssh/authorized_keys") linha(s) no arquivo)."

passo "Testando a chave de fora, sem implantar nada"
# Um SHA vazio: o cd-deploy.sh valida o formato ANTES de tocar no git, então
# ele recusa e sai 1 sem fazer deploy nenhum. Se esta mensagem chega até aqui,
# está provado que a chave autentica, que o comando forçado dispara o script
# certo e que o script roda — que é tudo o que falta ao CD. Testar com um SHA
# real implantaria de verdade, o que não é trabalho deste script.
saida="$(ssh -i "$CHAVE" \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new \
  -o BatchMode=yes \
  -o ConnectTimeout=15 \
  "$USUARIO_DEPLOY@$HOST" "deploy" 2>&1)" || true

if printf '%s' "$saida" | grep -q "SHA inválido"; then
  echo "  OK: a chave autentica e o comando forçado chama o cd-deploy.sh."
elif printf '%s' "$saida" | grep -q "Permission denied"; then
  erro "a chave não foi aceita. Confira o authorized_keys do $USUARIO_DEPLOY."
elif printf '%s' "$saida" | grep -qE "Connection timed out|Could not resolve|Connection refused"; then
  erro "não alcancei $HOST pela rede: $saida"
else
  erro "resposta inesperada do servidor (nada foi implantado):
$saida"
fi

passo "Pronto"
cat <<FIM
A chave existe agora em dois lugares, e só neles: o secret CD_SSH_KEY do
$REPO e o authorized_keys do $USUARIO_DEPLOY. A cópia local foi destruída.

Falta ainda, para o deploy chegar ao fim:

  2. o sudoers do $USUARIO_DEPLOY (/etc/sudoers.d/deploy-cd), senão o deploy
     falha no systemctl, depois de já ter mesclado e instalado;
  3. sudo $(dirname "$0")/provisionar.sh, para a unidade web passar a ter o
     ExecReload que o CD usa.

Com os três feitos, dispare sem esperar push nenhum. O CD pende do CI, e não
tem gatilho manual próprio, então quem se provoca à mão é o CI:

  gh workflow run ci.yml --repo $REPO && gh run watch --repo $REPO
FIM
