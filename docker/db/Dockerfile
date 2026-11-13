# PostgreSQL 18 (pinned) + pgBackRest + the backup scheduler (docs/02 §12, docs/06 Phase 10).
# WAL archiving runs inside the PostgreSQL server process, and pgBackRest must read the data
# directory, which is why the tool and its scheduler live in this image rather than in a
# separate container.
FROM postgres:18.6-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends pgbackrest \
 && rm -rf /var/lib/apt/lists/*

COPY --chmod=0755 hms-entrypoint.sh /usr/local/bin/hms-entrypoint.sh
COPY --chmod=0755 backup-loop.sh /usr/local/bin/hms-backup-loop.sh
COPY --chmod=0755 hms-dump.sh /usr/local/bin/hms-dump.sh
COPY --chmod=0755 hms-restore-test.sh /usr/local/bin/hms-restore-test.sh

ENTRYPOINT ["hms-entrypoint.sh"]
CMD ["postgres"]
