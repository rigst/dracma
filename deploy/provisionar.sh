#!/usr/bin/env bash
#
# Provisiona a Dracma neste servidor: serviços, nginx e certificado.
#
#   sudo /var/www/dracma/deploy/provisionar.sh
#
# Roda tudo que precisa de root. O que NÃO precisa — banco, venv, migrações,
# documentos legais, estáticos — é feito antes, fora daqui.
#
# É idempotente: pode rodar de novo. Reinstala as unidades, recarrega o nginx e
# só pede certificado novo se ainda não houver um.
set -euo pipefail

APP=dracma
DIR=/var/www/$APP
DOMINIO=dracma.stolben.com
PORTA=8015
WEBROOT=/var/www/certbot
SERVICOS=($APP ${APP}_celery ${APP}_celery_midia)

erro() { echo "ERRO: $*" >&2; exit 1; }
passo() { echo; echo "── $* ──"; }

[[ $EUID -eq 0 ]] || erro "rode com sudo."
[[ -d $DIR ]] || erro "$DIR não existe."
[[ -x $DIR/venv/bin/gunicorn ]] || erro "venv incompleto em $DIR/venv."
[[ -f $DIR/.env ]] || erro "$DIR/.env não existe."

# O .env tem segredos. 640, e não 600: o cd-deploy.sh roda o `manage.py` como
# o usuário "deploy", e o config/settings/base.py chama load_dotenv() a partir
# do cwd. Em 600 o dotenv não lê nada e não reclama, e o deploy morre dizendo
# que falta SECRET_KEY, que é a pista errada. Quem entra com o 640 é o grupo
# www-data: o servidor web que já serve este app, e o deploy. Para o resto da
# máquina continua ilegível. Mesmo modo do dojo e do sistema_trilhas.
chown rod:www-data "$DIR/.env"
chmod 640 "$DIR/.env"

passo "Unidades systemd"
install -m 644 "$DIR"/deploy/systemd/${APP}*.service /etc/systemd/system/
systemctl daemon-reload
for s in "${SERVICOS[@]}"; do
  systemctl enable --now "$s"
  echo "  $s: $(systemctl is-active "$s")"
done

passo "Esperando o gunicorn atender na porta $PORTA"
for _ in $(seq 1 15); do
  curl -sf -o /dev/null -H "Host: $DOMINIO" "http://127.0.0.1:$PORTA/login/" && break
  sleep 1
done
curl -sf -o /dev/null -H "Host: $DOMINIO" "http://127.0.0.1:$PORTA/login/" \
  || erro "o app não respondeu em $PORTA — veja: journalctl -u $APP -n 50"
echo "  respondeu."

mkdir -p "$WEBROOT"

if [[ ! -d /etc/letsencrypt/live/$DOMINIO ]]; then
  passo "Certificado ainda não existe: subindo nginx só com HTTP"
  # A config definitiva referencia o certificado. Instalada antes dele existir,
  # o `nginx -t` falha e o nginx nem recarrega — então o caminho é abrir só a
  # porta 80 o suficiente para o desafio do ACME passar.
  cat > /etc/nginx/sites-available/$APP <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMINIO;
    location /.well-known/acme-challenge/ { root $WEBROOT; }
    location / { return 200 "provisionando\n"; add_header Content-Type text/plain; }
}
NGINX
  ln -sf /etc/nginx/sites-available/$APP /etc/nginx/sites-enabled/$APP
  nginx -t && systemctl reload nginx

  passo "Emitindo o certificado"
  certbot certonly --webroot -w "$WEBROOT" -d "$DOMINIO" --non-interactive \
    || erro "certbot falhou. Confira se $DOMINIO aponta para este servidor."
else
  echo; echo "Certificado já existe para $DOMINIO — pulando a emissão."
fi

passo "nginx definitivo, com TLS"
install -m 644 "$DIR/deploy/nginx/$APP" /etc/nginx/sites-available/$APP
ln -sf /etc/nginx/sites-available/$APP /etc/nginx/sites-enabled/$APP
nginx -t && systemctl reload nginx

passo "Verificação"
for rota in /login/ /termos/ /privacidade/; do
  codigo=$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMINIO$rota")
  echo "  $codigo  $rota"
  [[ $codigo == 200 ]] || erro "https://$DOMINIO$rota respondeu $codigo."
done
redir=$(curl -s -o /dev/null -w '%{http_code}' "http://$DOMINIO/login/")
echo "  $redir  http → https"

echo
echo "Pronto. Falta só criar o seu usuário:"
echo "  cd $DIR && DJANGO_SETTINGS_MODULE=config.settings.production \\"
echo "    ./venv/bin/python manage.py createsuperuser"
