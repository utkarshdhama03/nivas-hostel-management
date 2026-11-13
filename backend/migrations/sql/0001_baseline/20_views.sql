-- =============================================================================
-- 20_views.sql - derived values (docs/03 §9).
-- security_invoker = true: RLS and privileges are checked for the role that
-- QUERIES the view, not for the view's owner. Without it, a view owned by
-- hms_owner could become a way around tenant isolation.
-- =============================================================================

-- Live state of every bed: inactive / maintenance / occupied / reserved / vacant.
-- "Occupied" and "reserved" are derived from bed_allocations and never stored,
-- so they can't drift out of sync. "Today" is the organization's local date.
CREATE VIEW bed_states WITH (security_invoker = true) AS
SELECT
  b.id        AS bed_id,
  b.org_id,
  b.hostel_id,
  b.room_id,
  CASE
    WHEN b.status = 'inactive'    OR r.status = 'inactive'    THEN 'inactive'
    WHEN b.status = 'maintenance' OR r.status = 'maintenance' THEN 'maintenance'
    WHEN EXISTS (SELECT 1 FROM bed_allocations a
                  WHERE a.bed_id = b.id AND a.status = 'active' AND a.stay @> d.local_today)
      THEN 'occupied'
    WHEN EXISTS (SELECT 1 FROM bed_allocations a
                  WHERE a.bed_id = b.id AND a.status = 'reserved'
                    AND (a.end_date IS NULL OR a.end_date > d.local_today))
      THEN 'reserved'
    ELSE 'vacant'
  END AS state
FROM beds b
JOIN rooms r         ON r.id = b.room_id
JOIN organizations o ON o.id = b.org_id
CROSS JOIN LATERAL (SELECT (now() AT TIME ZONE o.timezone)::date AS local_today) d;
