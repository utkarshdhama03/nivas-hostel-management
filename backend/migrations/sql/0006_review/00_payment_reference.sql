-- =============================================================================
-- 0006_review - fixes from the independent code review.
--
-- Should fix 2: a payment reference (UPI UTR, bank reference) must be unique among
-- payments that still stand, not among reversed ones. Q31's correction path is
-- "reverse the wrong payment, then record it correctly", and the real reference
-- must be usable again. A partial unique index can't express "not reversed"
-- (payments are append-only; the reversal is a row in payment_reversals), so a
-- trigger does it. The advisory lock serializes inserts of the same reference,
-- so two concurrent payments can't both pass the check. The error keeps the old
-- constraint name, so the API still answers 409 duplicate_payment_reference.
-- =============================================================================
DROP INDEX payments_reference_uq;
CREATE INDEX payments_reference_idx ON payments (org_id, method, reference) WHERE reference IS NOT NULL;

CREATE FUNCTION payments_reference_unique() RETURNS trigger
  LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
  IF NEW.reference IS NULL THEN
    RETURN NEW;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.org_id::text || '/' || NEW.method || '/' || NEW.reference, 0));
  IF EXISTS (SELECT 1 FROM payments p
              WHERE p.org_id = NEW.org_id AND p.method = NEW.method AND p.reference = NEW.reference
                AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)) THEN
    RAISE EXCEPTION 'payment reference already recorded'
      USING ERRCODE = 'unique_violation', CONSTRAINT = 'payments_reference_uq';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER payments_reference_unique BEFORE INSERT ON payments
  FOR EACH ROW EXECUTE FUNCTION payments_reference_unique();
