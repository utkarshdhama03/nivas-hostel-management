-- =============================================================================
-- 0016_remove_inventory (12 November 2026, D85): inventory and assets are gone from the product.
--
-- Drops the four tables (with their indexes, RLS policies, triggers and grants) and the trigger
-- function that kept quantity_on_hand in step with stock_movements. The downgrade
-- (down/00_restore.sql) recreates the structure exactly; the deleted rows don't come back.
-- Old audit entries about items and assets stay: the audit log is never edited.
-- =============================================================================

DROP TABLE asset_assignments;
DROP TABLE assets;
DROP TABLE stock_movements;
DROP TABLE inventory_items;
DROP FUNCTION apply_stock_movement();
