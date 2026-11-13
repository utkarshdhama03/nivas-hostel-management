#!/bin/bash
# Daily logical dump of "hms" (docs/02 §12): independent of pgBackRest, so a damaged repository
# still leaves a usable copy. Encrypted with the repository cipher pass (it holds personal data),
# kept 14 days. Prints the size of the new file on its last line.
set -euo pipefail
DIR=/var/lib/pgbackrest/dumps
mkdir -p "$DIR"
OUT="$DIR/hms-$(date -u +%Y%m%d-%H%M%S).dump.enc"
pg_dump -U postgres -d hms -Fc \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:PGBACKREST_REPO1_CIPHER_PASS -out "$OUT.part"
mv "$OUT.part" "$OUT"
find "$DIR" -name 'hms-*.dump.enc' -mtime +14 -delete
stat -c %s "$OUT"
