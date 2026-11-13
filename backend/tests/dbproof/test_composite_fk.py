"""Composite foreign keys stop cross-organization references (docs/05 MT-3).

Why this matters: PostgreSQL checks foreign keys WITHOUT applying RLS. A plain
`bed_id REFERENCES beds(id)` would happily accept Org B's bed in Org A's
allocation if the application passed the wrong id. Our FKs include org_id,
e.g. (org_id, hostel_id, bed_id) -> beds(org_id, hostel_id, id), so such a row
cannot exist.

How the test isolates the FK:
  * runs as superuser, so RLS is not what stops it;
  * `ALTER TABLE ... DISABLE TRIGGER USER` turns off our own triggers (guards,
    append-only) but NOT the internal triggers that implement foreign keys;
  * everything happens inside a transaction that is rolled back.
For each FK it rewrites one real Org-A child row so that its uuid reference
columns point at an Org-B parent (org_id stays A) and expects error 23503.
"""

from typing import Any

import psycopg
import pytest
from psycopg import sql

from .helpers import FOREIGN_KEY_VIOLATION
from .world import World

pytestmark = pytest.mark.dbproof

FK_QUERY = """
SELECT con.conname,
       child.relname  AS child,
       ARRAY(SELECT a.attname FROM unnest(con.conkey)  WITH ORDINALITY k(n, i)
             JOIN pg_attribute a ON a.attrelid = con.conrelid  AND a.attnum = k.n ORDER BY k.i) AS child_cols,
       parent.relname AS parent,
       ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i)
             JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i) AS parent_cols,
       ARRAY(SELECT format_type(a.atttypid, a.atttypmod) FROM unnest(con.conkey) WITH ORDINALITY k(n, i)
             JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i) AS col_types
FROM pg_constraint con
JOIN pg_class child  ON child.oid  = con.conrelid
JOIN pg_class parent ON parent.oid = con.confrelid
JOIN pg_namespace n  ON n.oid = child.relnamespace
WHERE con.contype = 'f' AND n.nspname = 'public' AND cardinality(con.conkey) > 1
ORDER BY child.relname, con.conname
"""

# Errors that mean "another constraint fired first; try a different Org-B parent".
TRY_NEXT = {"23P01", "23505", "23514"}


def ident_list(cols: list[str]) -> sql.Composable:
    return sql.SQL(", ").join(map(sql.Identifier, cols))


def test_composite_fk_rejects_cross_org_reference(su: psycopg.Connection[Any], world_a: World, world_b: World) -> None:
    fks = su.execute(FK_QUERY).fetchall()
    assert len(fks) > 80, f"expected the full set of composite FKs, found {len(fks)}"
    failures: list[str] = []

    for name, child, child_cols, parent, parent_cols, col_types in fks:
        if "org_id" not in child_cols:
            failures.append(f"{name}: composite FK without org_id")
            continue
        # uuid columns are the references we redirect to Org B; text columns (category,
        # mime type) must keep matching, so the Org-B parent is chosen to agree on them.
        redirect = [
            (c, p) for c, p, t in zip(child_cols, parent_cols, col_types, strict=True) if t == "uuid" and c != "org_id"
        ]
        keep = [(c, p) for c, p, t in zip(child_cols, parent_cols, col_types, strict=True) if t != "uuid"]

        with su.transaction(force_rollback=True):
            su.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(sql.Identifier(child)))
            not_null = sql.SQL(" AND ").join(sql.SQL("{} IS NOT NULL").format(sql.Identifier(c)) for c in child_cols)
            row = su.execute(
                sql.SQL("SELECT ctid, {} FROM {} WHERE org_id = %s AND {} LIMIT 1").format(
                    ident_list(child_cols), sql.Identifier(child), not_null
                ),
                (world_a.org,),
            ).fetchone()
            if row is None:
                failures.append(f"{name}: no Org-A row in {child} with {child_cols} set (seed is incomplete)")
                continue
            ctid, values = row[0], dict(zip(child_cols, row[1:], strict=True))

            where_keep = [sql.SQL("{} = %s").format(sql.Identifier(p)) for _, p in keep]
            cond = sql.SQL(" AND ").join([sql.SQL("org_id = %s"), *where_keep])
            candidates = su.execute(
                sql.SQL("SELECT {} FROM {} WHERE {} LIMIT 25").format(
                    ident_list([p for _, p in redirect]), sql.Identifier(parent), cond
                ),
                (world_b.org, *[values[c] for c, _ in keep]),
            ).fetchall()

            outcome = None
            for cand in candidates:
                assignments = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c, _ in redirect)
                try:
                    with su.transaction():  # savepoint per attempt
                        su.execute(
                            sql.SQL("UPDATE {} SET {} WHERE ctid = %s").format(sql.Identifier(child), assignments),
                            (*cand, ctid),
                        )
                    outcome = "ACCEPTED (cross-org reference was stored!)"
                    break
                except psycopg.Error as exc:
                    if exc.sqlstate == FOREIGN_KEY_VIOLATION:
                        outcome = "ok"
                        break
                    if exc.sqlstate not in TRY_NEXT:
                        outcome = f"unexpected {exc.sqlstate}: {exc}"
                        break
            if outcome != "ok":
                failures.append(f"{name} ({child} -> {parent}): {outcome or 'no usable Org-B candidate'}")

    assert not failures, "\n".join(failures)
