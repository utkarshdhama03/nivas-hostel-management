#!/bin/bash
# Wraps the official entrypoint: writes the pgBackRest configuration (no secrets in it), starts
# the backup scheduler in the background as postgres, then hands over to PostgreSQL.
set -euo pipefail
mkdir -p /etc/pgbackrest /var/lib/pgbackrest /var/log/pgbackrest
cat > /etc/pgbackrest/pgbackrest.conf <<'CONF'
[global]
repo1-path=/var/lib/pgbackrest
repo1-cipher-type=aes-256-cbc
repo1-retention-full=4
start-fast=y
log-path=/var/log/pgbackrest
log-level-file=info

[hms]
pg1-path=/var/lib/postgresql/18/docker
pg1-socket-path=/var/run/postgresql
pg1-user=postgres
CONF
chown -R postgres:postgres /var/lib/pgbackrest /var/log/pgbackrest /etc/pgbackrest
if [ -z "${PGBACKREST_REPO1_CIPHER_PASS:-}" ]; then
  echo "hms-entrypoint: PGBACKREST_REPO1_CIPHER_PASS is not set: backups are disabled" >&2
else
  /usr/local/bin/hms-backup-loop.sh &   # as root; it drops to postgres for database work
fi
exec docker-entrypoint.sh "$@"
