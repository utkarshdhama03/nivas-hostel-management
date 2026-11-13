# Runbook: backups and restore

For whoever has to bring the data back: you, or someone helping you. Read the whole page once before you need it.

> **Warning: by default, backups are on the same disk as the database.**
> Until `HMS_BACKUP_DIR` in `.env` points to a second internal drive or an external disk (Q5), the backup repository is a Docker volume (`hms_backups`) on the **same physical disk** as the database. That protects against mistakes (a bad UPDATE, a deleted record, a broken migration) but **not against disk failure, theft or a dead laptop**: the database and its backups would be lost together.
> The Platform health page shows a warning while this is the case.
> To fix it, set `HMS_BACKUP_DIR=E:/hms-backups` (your drive letter) in `.env`, then run `docker compose up -d db`. The next check of the backup loop (at most 15 minutes) takes a new full backup there.

## What is backed up, and when

Backups run **inside the database container** (`docker/db/backup-loop.sh`). There are no fixed times, because the laptop is often asleep or off:

- When the container starts, and then every 15 minutes, each kind of backup checks its last **successful** run in `backup_runs`.
- If that run is older than the interval in the table below (or has never succeeded), the backup runs **straight away**.
- So a laptop that slept through the night catches up within minutes of waking.

| Kind | Interval | What |
|---|---|---|
| WAL archiving | continuous | Every change, so you can restore to any moment since the oldest full backup (PITR) |
| Full (pgBackRest) | 7 days | The base for PITR; the 4 most recent fulls are kept (about 4 weeks) |
| Differential | 1 day | Changes since the last full |
| Verify | 1 day | `pgbackrest verify` checks the repository's checksums |
| Logical dump | 1 day | `pg_dump` of `hms`, encrypted, kept 14 days, independent of pgBackRest |
| File copy | 1 day | The uploads volume (photos, documents) copied to the backup location; files are write-once |
| Restore test | 7 days | Restores the latest backup into a throwaway directory, starts it and runs sanity queries |

Everything is recorded in `backup_runs` and shown on **Platform → Health**: last backup, last restore test, and WAL archiving.

**Keys:** the pgBackRest repository and the dumps are encrypted with `PGBACKREST_REPO1_CIPHER_PASS` from `hms-secrets/pgbackrest.env`. **Without that file the backups can't be read.** Keep an offline copy together with the data keyring (see [keys.md](keys.md)).

## Check that backups are healthy

1. Platform → Health: "Last backup", "Last restore test" and "WAL archiving" should be `ok`.
2. Or run: `docker compose exec -u postgres db pgbackrest --stanza=hms info`

## Practise first: the PITR drill (safe)

`./scripts/drill-pitr.sh` writes 100 rows to a scratch database and notes the time T. It then runs a "bad" UPDATE and restores the cluster to T **into a throwaway directory**, never the live database. It prints the restored sum: 100 means the data came back. Run it after any change to the backup setup.

## Real restore 1: undo a mistake (point in time)

Use this when data was damaged at a known time, for example a wrong UPDATE at 14:32. This **replaces the live database** with its state at the chosen moment; everything after that moment is lost. If unsure, practise with the drill first.

1. **Stop the app** so nothing writes: `docker compose stop api worker web`
2. **Pick the target time** (UTC, a little before the mistake), e.g. `2026-11-08 09:01:00+00`.
3. **Stop PostgreSQL but keep the container:**
   `docker compose exec -u postgres db pg_ctl -D /var/lib/postgresql/18/docker -m fast stop`
   The container stops with it. Start a maintenance shell on the same volumes:
   `docker compose run --rm --entrypoint bash db`
4. **Restore in place** inside that shell, as postgres:
   `gosu postgres pgbackrest --stanza=hms --delta --type=time "--target=2026-11-08 09:01:00+00" --target-action=promote restore`
   (`--delta` rewrites only the changed files.)
5. Exit the shell and **start everything:** `docker compose up -d`
6. **Check** that the data is as expected, then look at Platform → Health. Take a full backup now, because the restore started a new timeline:
   `docker compose exec -u postgres db pgbackrest --stanza=hms --type=full backup`
7. **Files:** uploads are write-once, so files from after the target time simply have no database row and are purged as unattached after 24 hours. Nothing needs restoring.

## Real restore 2: the disk or laptop is gone

You need the backup drive, `hms-secrets` (from the USB copy) and a computer with Docker Desktop.

1. Install Docker Desktop, copy the project and the `hms-secrets` folder, and set `.env` (`HMS_SECRETS_DIR`, `HMS_BACKUP_DIR` = the backup drive).
2. `docker compose build db && docker compose run --rm --entrypoint bash db`, then in that shell:
   `gosu postgres pgbackrest --stanza=hms restore` (restores to the end of the archive)
3. Exit, then `docker compose up -d`.
4. **Uploads:** copy the backup drive's `files/` folder back into the uploads volume. Git Bash doesn't read `.env`, so first run `export HMS_BACKUP_DIR="E:/hms-backups"` (the same path as in `.env`), then:
   `docker compose run --rm -v "$HMS_BACKUP_DIR/files:/from:ro" api sh -c "cp -a -n /from/. /data/uploads/"`
5. Check Platform → Health and open one student's identity document. That needs the data keyring; see [keys.md](keys.md).

## If pgBackRest itself is broken: the logical dump

`docker compose exec db ls /var/lib/pgbackrest/dumps` shows the dumps. To restore one into a fresh database (this needs the cipher pass in the container's environment):

```
docker compose exec db bash -c 'createdb -U postgres -O hms_owner hms_restored && \
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:PGBACKREST_REPO1_CIPHER_PASS \
  -in /var/lib/pgbackrest/dumps/hms-YYYYMMDD-HHMMSS.dump.enc | pg_restore -U postgres -d hms_restored'
```
Check `hms_restored`, then swap names while the app is stopped (`ALTER DATABASE hms RENAME TO hms_old; ALTER DATABASE hms_restored RENAME TO hms;`).

A dump is a snapshot at its own moment only; it has no point-in-time recovery.
