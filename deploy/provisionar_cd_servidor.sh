#!/usr/bin/env bash
#
# Prepara o SERVIDOR para o deploy contínuo da Dracma.
#
#   sudo /var/www/dracma/deploy/provisionar_cd_servidor.sh
#
# O par de chaves é o outro script (provisionar_cd.sh, rodado sem sudo). Este
# aqui cuida do que o usuário "deploy" precisa para o cd-deploy.sh chegar ao
# fim. São quatro coisas, e o CD morre em cada uma delas separadamente:
#
#   1. escrever na árvore. O dojo e o trilhas são `rod:www-data` 2775, e por
#      isso o deploy (grupo www-data) escreve neles. A dracma nasceu `rod:rod`,
#      então `git merge`, `pip install` e `collectstatic` parariam em
#      "Permission denied";
#   2. ler o .env. O cd-deploy.sh roda `manage.py` COMO deploy, e o
#      config/settings/base.py chama load_dotenv() a partir do cwd. Com o .env
#      em 600 o dotenv não lê nada, não reclama, e o `check --deploy` morre
#      dizendo que falta SECRET_KEY, que é a pista errada;
#   3. o git aceitar o repositório. Rodando como deploy num repo de outro dono,
#      o git recusa com "detected dubious ownership" antes de qualquer fetch.
#      Os nove apps já implantados estão no ~deploy/.gitconfig; a dracma não;
#   4. o sudo do reload/restart e do backup pré-migração. Sem isso o deploy
#      falha DEPOIS de mesclar e instalar, com o processo antigo ainda no ar.
#
# É idempotente: pode rodar de novo. O bloco no sudoers é trocado pelos
# marcadores, não empilhado, e o resto converge para o mesmo estado.
#
# Ver rigst/ci RUNBOOK.md seções 7.1 e 7.2.
set -euo pipefail

APP=dracma
DIR=/var/www/dracma
USUARIO_DEPLOY=deploy
DONO=rod
GRUPO=www-data
SUDOERS=/etc/sudoers.d/deploy-cd
SERVICO_WEB=dracma.service
SERVICOS_RESTART=(dracma.service dracma_celery.service dracma_celery_midia.service)
BACKUP="$DIR/deploy/backup_postgres.sh"
SYSTEMCTL=/usr/bin/systemctl

erro() { echo "ERRO: $*" >&2; exit 1; }
passo() { echo; echo "── $* ──"; }

[[ $EUID -eq 0 ]] || erro "rode com sudo."
[[ -d $DIR ]] || erro "$DIR não existe."
id "$USUARIO_DEPLOY" >/dev/null 2>&1 \
  || erro "usuário '$USUARIO_DEPLOY' não existe. Ver RUNBOOK.md seção 7.1."
id "$DONO" >/dev/null 2>&1 || erro "usuário '$DONO' não existe."
[[ -x $BACKUP ]] || erro "$BACKUP não existe ou não é executável."
[[ -x $SYSTEMCTL ]] || erro "$SYSTEMCTL não encontrado."
for unidade in "${SERVICOS_RESTART[@]}"; do
  [[ -f /etc/systemd/system/$unidade ]] \
    || erro "unidade $unidade não instalada. Rode antes: sudo $DIR/deploy/provisionar.sh"
done

# A árvore do sudoers precisa estar válida ANTES, senão a checagem do fim não
# consegue distinguir "este script quebrou" de "já estava quebrado". Foi o que
# aconteceu na primeira execução: o bloco novo passou sozinho no visudo, a
# árvore reprovou, e a mensagem acusou o script de um estrago que não era dele.
if ! visudo -cq 2>/dev/null; then
  echo "ERRO: o /etc/sudoers JÁ estava inválido antes deste script." >&2
  echo "Nada foi alterado. O que o visudo aponta:" >&2
  echo >&2
  visudo -c 2>&1 | sed 's/^/  /' >&2
  echo >&2
  echo "Conserte o arquivo apontado acima e rode este script de novo." >&2
  exit 1
fi

passo "1/4 Árvore gravável pelo grupo $GRUPO"
# O setgid nos diretórios é o que faz durar: sem ele, o primeiro diretório que
# o deploy criar (um __pycache__, uma pasta nova de app) nasce no grupo dele e
# o rod perde a escrita ali, em silêncio, meses depois.
chown -R "$DONO:$GRUPO" "$DIR"
chmod -R g+w "$DIR"
find "$DIR" -type d -exec chmod g+s {} +
echo "  $(stat -c '%A %U:%G' "$DIR") $DIR"

passo "2/4 .env legível pelo grupo, e só por ele"
# 640, como no dojo e no trilhas. É uma folga deliberada em relação ao 600 de
# hoje: quem entra junto é o grupo www-data, isto é, o próprio servidor web
# que já serve este app, e o deploy. Não vira legível para o resto da máquina.
chown "$DONO:$GRUPO" "$DIR/.env"
chmod 640 "$DIR/.env"
echo "  $(stat -c '%A %U:%G' "$DIR/.env") $DIR/.env"

passo "3/4 Repositório declarado seguro para o $USUARIO_DEPLOY"
if sudo -u "$USUARIO_DEPLOY" git config --global --get-all safe.directory 2>/dev/null \
    | grep -qx "$DIR"; then
  echo "  já estava declarado."
else
  sudo -u "$USUARIO_DEPLOY" git config --global --add safe.directory "$DIR"
  echo "  declarado em ~$USUARIO_DEPLOY/.gitconfig."
fi

passo "4/4 Sudoers do $USUARIO_DEPLOY"
# Arquivo compartilhado pelos apps da frota, então o bloco da dracma é trocado
# entre marcadores em vez de o arquivo ser reescrito: um `>` aqui apagaria o
# CD dos outros nove.
INICIO="# ${APP} (inicio) - gerado por ${DIR}/deploy/$(basename "$0")"
MARCA_FIM="# ${APP} (fim)"

CANDIDATO="$(mktemp)"
COPIA="$(mktemp)"
trap 'rm -f "$CANDIDATO" "$COPIA"' EXIT

if [[ -f $SUDOERS ]]; then
  cp -p "$SUDOERS" "$COPIA"
  # Copia tudo menos o bloco antigo deste app.
  awk -v ini="$INICIO" -v fim="$MARCA_FIM" '
    $0 == ini { dentro = 1; next }
    $0 == fim { dentro = 0; next }
    !dentro   { print }
  ' "$SUDOERS" > "$CANDIDATO"
else
  : > "$COPIA"
  printf '# Sudo do deploy contínuo. Um bloco por app, gerado pelo script de\n' > "$CANDIDATO"
  printf '# cada projeto. Ver rigst/ci RUNBOOK.md seção 7.2.\n' >> "$CANDIDATO"
fi

{
  printf '%s\n' "$INICIO"
  # Uma linha por comando, e não uma linha com vírgulas: o sudoers casa por
  # string exata, e assim dá para ler no `sudo -l` qual falta sem contar
  # vírgula. O reload é o que o CD usa; o restart fica porque o cd-deploy.sh
  # troca para ele quando o gunicorn sobe de versão (RUNBOOK 7.1.2), e serve
  # para depuração à mão sem precisar de outra regra depois.
  printf '%s ALL=(ALL) NOPASSWD: %s reload %s\n' "$USUARIO_DEPLOY" "$SYSTEMCTL" "$SERVICO_WEB"
  for unidade in "${SERVICOS_RESTART[@]}"; do
    printf '%s ALL=(ALL) NOPASSWD: %s restart %s\n' "$USUARIO_DEPLOY" "$SYSTEMCTL" "$unidade"
  done
  # Como rod, não como deploy: os dumps vivem em /home/rod/backups, 0750 do
  # rod, de onde o rclone os manda para o Drive.
  printf '%s ALL=(%s) NOPASSWD: %s\n' "$USUARIO_DEPLOY" "$DONO" "$BACKUP"
  printf '%s\n' "$MARCA_FIM"
} >> "$CANDIDATO"

# Confere a sintaxe ANTES de instalar. Um erro em qualquer arquivo de
# /etc/sudoers.d derruba o sudo inteiro, para todo mundo, na hora.
visudo -cqf "$CANDIDATO" || erro "o arquivo gerado não passou no visudo. Nada foi instalado."

install -m 0440 -o root -g root "$CANDIDATO" "$SUDOERS"

# E confere a árvore inteira depois, porque o teste acima valida um arquivo
# isolado. Se algo estiver errado agora, desfaz enquanto o sudo desta sessão
# ainda funciona.
if ! visudo -cq 2>/dev/null; then
  motivo="$(visudo -c 2>&1 || true)"
  if [[ -s $COPIA ]]; then
    install -m 0440 -o root -g root "$COPIA" "$SUDOERS"
  else
    rm -f "$SUDOERS"
  fi
  echo "ERRO: o /etc/sudoers ficou inválido e foi revertido. Nada mudou no sudo." >&2
  echo "O que o visudo apontou, com o arquivo novo no lugar:" >&2
  printf '%s\n' "$motivo" | sed 's/^/  /' >&2
  exit 1
fi
echo "  $SUDOERS instalado e validado."

passo "Verificação, como o deploy vai encontrar"
teste="$DIR/.teste_cd_$$"
sudo -u "$USUARIO_DEPLOY" touch "$teste" 2>/dev/null \
  && rm -f "$teste" && echo "  escreve na árvore: ok" \
  || erro "o $USUARIO_DEPLOY ainda não escreve em $DIR."
sudo -u "$USUARIO_DEPLOY" test -r "$DIR/.env" \
  && echo "  lê o .env: ok" || erro "o $USUARIO_DEPLOY ainda não lê o .env."
sudo -u "$USUARIO_DEPLOY" git -C "$DIR" rev-parse HEAD >/dev/null \
  && echo "  o git aceita o repositório: ok" \
  || erro "o git ainda recusa $DIR para o $USUARIO_DEPLOY."
echo
echo "  o que o $USUARIO_DEPLOY pode rodar com sudo, para este app:"
sudo -n -l -U "$USUARIO_DEPLOY" 2>/dev/null | grep -F "$APP" | sed 's/^/    /'

cat <<TEXTO

Pronto. Falta o passo 3, que é reinstalar a unidade web para ela ganhar o
ExecReload novo (hoje o systemctl reload recusa com "Job type reload is not
applicable"):

  sudo $DIR/deploy/provisionar.sh

Depois disso o CD está completo. Para provar de ponta a ponta sem esperar
push nenhum, provoque o CI, que é de quem o CD pende:

  gh workflow run ci.yml --repo rigst/$APP && gh run watch --repo rigst/$APP
TEXTO
