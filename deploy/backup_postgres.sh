#!/usr/bin/env bash
set -euo pipefail

# Porta de /var/www/sistema_arq/shared/scripts/backup_postgres.sh, adaptado
# para o .env de dracma (DB_NAME/DB_USER/... em vez de DATABASE_URL)
# e para o checkout direto (sem shared/) — os dumps ficam fora do working
# tree do git, em /home/rod/backups, para não interagir com git clean/status.

APP="dracma"
ENV_FILE="/var/www/dracma/.env"
BACKUP_DIR="/home/rod/backups/$APP/postgres"
LOG_DIR="/home/rod/backups/$APP/logs"
LOG_FILE="$LOG_DIR/postgres-backup.log"
LOCK_FILE="/tmp/${APP}_postgres_backup.lock"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

# Lê uma chave do .env sem executá-lo: sourcear quebra em senha com
# caractere especial de shell (aconteceu com divisor_pdf, que tem vírgula
# e ponto na senha). Pega a última ocorrência, tira aspas e CR.
read_env_var() {
  local key="$1" file="$2" val
  val="$(grep -E "^${key}=" "$file" | tail -n1 || true)"
  val="${val#"${key}="}"
  val="${val%$'\r'}"
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"
  printf '%s' "$val"
}

{
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] backup start"

  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERRO: arquivo de ambiente nao encontrado: $ENV_FILE"
    exit 1
  fi

  db_name="$(read_env_var DB_NAME "$ENV_FILE")"
  db_user="$(read_env_var DB_USER "$ENV_FILE")"
  db_pass="$(read_env_var DB_PASSWORD "$ENV_FILE")"
  db_host="$(read_env_var DB_HOST "$ENV_FILE")"; db_host="${db_host:-localhost}"
  db_port="$(read_env_var DB_PORT "$ENV_FILE")"; db_port="${db_port:-5432}"

  if [[ -z "$db_name" || -z "$db_user" ]]; then
    echo "ERRO: DB_NAME/DB_USER ausentes em $ENV_FILE"
    exit 1
  fi

  timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
  outfile="$BACKUP_DIR/${db_name}_${timestamp}.dump"
  tmpfile="$outfile.tmp"

  flock -n 9 || { echo "Backup ja em execucao, saindo"; exit 0; }

  PGPASSWORD="$db_pass" pg_dump \
    --format=custom --no-owner --no-privileges \
    -h "$db_host" -p "$db_port" -U "$db_user" -d "$db_name" \
    --file "$tmpfile"

  pg_restore -l "$tmpfile" >/dev/null
  mv "$tmpfile" "$outfile"
  chmod 600 "$outfile"

  find "$BACKUP_DIR" -type f -name '*.dump' -mtime +"$RETENTION_DAYS" -delete

  latest_size="$(du -h "$outfile" | awk '{print $1}')"
  echo "Backup concluido: $outfile ($latest_size)"
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] backup end"
} >> "$LOG_FILE" 2>&1 9> "$LOCK_FILE"
