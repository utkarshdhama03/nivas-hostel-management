-- =============================================================================
-- 0004_scale - found by the Phase 10 scale test (D65).
--
-- The organization audit log pages by id (uuidv7, time-ordered) under RLS
-- (org_id = current organization). Without an (org_id, id) index PostgreSQL walks
-- the primary key backwards through every other organization's newer rows: for a
-- quiet organization among 50 busy ones, 480,000 rows and 576 ms for one page.
-- With this index: 0.1 ms.
-- =============================================================================
CREATE INDEX audit_logs_org_id_id_idx ON audit_logs (org_id, id DESC);
