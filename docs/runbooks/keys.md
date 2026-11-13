# Runbook: keys (keep, rotate, recover)

For whoever looks after the server. Read it once now; you won't have time when a key is already lost.

> **Warning: by default, backups are on the same disk as the database.**
> Until `HMS_BACKUP_DIR` in `.env` points to a second internal drive or an external disk, a disk failure loses the database **and** its backups together. Keys don't help with that. See [restore.md](restore.md) for the fix (one line in `.env`).

## The two secrets that matter

Both live in `hms-secrets` (the folder `HMS_SECRETS_DIR` points to, for example `C:\Users\<you>\hms-secrets\`), **outside the project folder**. Neither is ever in Git, the database, the uploads or the backups.

| File | What it unlocks | If it is lost |
|---|---|---|
| `data-keys.json` | Students' identity documents | Documents under the lost key are **gone for good** and must be collected again; their database rows (type, masked number) survive. Everything else is unaffected. |
| `pgbackrest.env` | The pgBackRest repository and the daily encrypted dumps | **Every existing backup is unreadable.** The live database is fine: set a new pass and take a new full backup immediately (below). |

The other files in `hms-secrets` (`hms-admin.env`, `hms-app.env`) hold database passwords. They can be reset if lost, but keep them with the rest.

## Keep an offline copy (do this now, and after every change)

1. Copy the **whole** `hms-secrets` folder to a USB drive. Store the drive away from the laptop and away from the backup disk. A thief or a fire should not get all three.
2. **New in Phase 10:** `pgbackrest.env` was added. If your USB copy is older than that, refresh it today.
3. Refresh the USB copy the **same day** you add or retire a data key, or change the backup pass.
4. Optional: print `data-keys.json` and keep it in a sealed envelope.
5. Never put a copy in cloud storage, email, chat, Git or the backup folder.

## Check the copy works (drill 2)

The automated test `test_document_opens_with_keyring_restored_from_copy` does this in code on every quality-gate run. To do it by hand on a second computer, follow "the disk or laptop is gone" in [restore.md](restore.md), using `hms-secrets` from the USB drive, and open one student's identity document. If it opens, the copy is good.

## Rotate the data key

Do this when a key may have leaked, or once a year as routine. Nothing stops working at any step: every encrypted item records the ID of the key it uses, and the old key stays in the file until nothing uses it.

Run these from the project folder in Git Bash, with the stack up. Git Bash doesn't read `.env` (only Docker Compose does), so first give the shell the same path as in `.env`; without this the commands below mount the wrong folder:
```
export HMS_SECRETS_DIR="C:/Users/<you>/hms-secrets"
```

1. **Add a new key and make it active.** The old key stays, for reading.
   ```
   docker compose run --rm -v "$HMS_SECRETS_DIR:/keys" tools python -m app.cli add-data-key --keyring /keys/data-keys.json
   ```
   It prints only the new key ID (like `k20261109-3fa2`), never key material.
2. **Restart the API and the worker** so they load the new keyring: `docker compose restart api worker`. From now on, new documents use the new key.
3. **Re-encrypt everything still under an old key:**
   ```
   docker compose run --rm tools python -m app.cli reencrypt-data
   ```
   Each document is written to a new file, the database row is switched in one transaction, and only then is the old file deleted. A crash at any point leaves every row readable. It is safe to run again; a second run reports 0.
4. **Check:** `docker compose run --rm tools python -m app.cli data-key-status` should show 0 items for the old key ID.
   **Retire the old key** (refused while anything still uses it):
   ```
   docker compose run --rm -v "$HMS_SECRETS_DIR:/keys" tools python -m app.cli retire-data-key --keyring /keys/data-keys.json --kid <old key ID>
   ```
   Restart the API and the worker again.
5. **Refresh the USB copy.**

Old **backups** still contain ciphertext under the retired key. If you need to restore a backup older than the rotation, use a keyring copy from before step 4. So for a routine rotation, keep the previous USB copy until the oldest backup is newer than the rotation, which is about 4 weeks (the retention of full backups).

If the key **leaked**, also treat the documents encrypted with it as exposed. Rotation stops future exposure; it can't undo a past one.

## Change the backup pass (`pgbackrest.env`)

pgBackRest can't re-encrypt an existing repository. Changing the pass means starting a new repository:

1. `docker compose stop db`
2. Move the old repository aside (it stays readable with the **old** pass, so keep that too until the new repository has a full backup and the old one is no longer needed): rename the folder `HMS_BACKUP_DIR` points to, or for the default volume run `docker volume create hms_backups_old` and copy into it.
3. Put the new pass in `pgbackrest.env` (`PGBACKREST_REPO1_CIPHER_PASS=` followed by a long random value; one way is `openssl rand -base64 48`). Don't print it into a shared terminal.
4. `docker compose up -d db`. The backup loop creates the stanza and, finding no successful full backup in the new repository, takes one within minutes.
5. Refresh the USB copy.

## If a key is lost

- **`data-keys.json` lost, USB copy fine:** copy it back to `hms-secrets`, `docker compose restart api worker`. Done.
- **Both copies lost:** the app refuses to start without a valid keyring. Before going further, search every drive and old USB stick for `data-keys.json`: what follows can't be undone. Then write a new `data-keys.json` with a **new** key ID, as in README step 1 (for example `{"active": "k20261109-new", "keys": {"k20261109-new": "<base64 of 32 random bytes>"}}`), and start the app. Consequences: every identity document under the lost key is unreadable and must be uploaded again. To see how many items are affected, run `docker compose run --rm tools python -m app.cli data-key-status`; it lists each key ID with its item count and marks IDs that are missing from the keyring.
- **`pgbackrest.env` lost:** existing backups are unusable. Follow "Change the backup pass" from step 3, then take a full backup. The live database is not affected.
