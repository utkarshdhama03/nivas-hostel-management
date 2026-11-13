"""Phase 0 database proof suite (docs/06 §2.3).

These tests use plain SQL through psycopg, with no application code, to prove that the
schema itself enforces the rules in docs/03: RLS isolation, composite foreign keys,
exclusion constraints, ledger triggers, grants and the Platform Owner's window.
"""
