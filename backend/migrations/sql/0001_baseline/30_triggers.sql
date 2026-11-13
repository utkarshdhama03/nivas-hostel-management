-- =============================================================================
-- 30_triggers.sql - rules a CHECK constraint cannot express (docs/03 §7).
--
-- A CHECK sees only the row being written. Rules that depend on OTHER rows
-- ("deposit held never goes negative", "a payment's entries add up") need a
-- trigger. Two kinds are used:
--   * BEFORE triggers: run as each row is written; can lock rows to serialize.
--   * Deferred CONSTRAINT triggers: run at COMMIT, after the whole transaction's
--     rows exist; used for "everything must add up" rules.
-- Errors carry a CONSTRAINT name so the API can map them to error codes
-- (docs/04 §7).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Append-only history tables
-- -----------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ledger_entries', 'payments', 'payment_reversals', 'stock_movements',
                           'complaint_updates', 'audit_logs']
  LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I
                    FOR EACH ROW EXECUTE FUNCTION forbid_change()', t || '_append_only', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I
                    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change()', t || '_no_truncate', t);
  END LOOP;
END $$;

-- qr_redemptions: never updated; the worker may DELETE old rows (purge after 2 days).
CREATE TRIGGER qr_redemptions_no_update BEFORE UPDATE ON qr_redemptions
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER qr_redemptions_no_truncate BEFORE TRUNCATE ON qr_redemptions
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- gate_events and ledger_allocations: append-only except one one-time change.
CREATE TRIGGER gate_events_no_delete BEFORE DELETE ON gate_events
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER gate_events_no_truncate BEFORE TRUNCATE ON gate_events
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER ledger_allocations_no_delete BEFORE DELETE ON ledger_allocations
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER ledger_allocations_no_truncate BEFORE TRUNCATE ON ledger_allocations
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- A gate override is acknowledged exactly once, and nothing else may change.
-- to_jsonb(row) - 'col' compares "all columns except these" in one expression.
CREATE FUNCTION gate_event_ack_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.acknowledged_at IS NOT NULL THEN
    RAISE EXCEPTION 'gate event % is already acknowledged', OLD.id
      USING ERRCODE = 'restrict_violation', CONSTRAINT = 'gate_events_ack_once';
  END IF;
  IF (to_jsonb(NEW) - 'acknowledged_by' - 'acknowledged_at')
     IS DISTINCT FROM (to_jsonb(OLD) - 'acknowledged_by' - 'acknowledged_at') THEN
    RAISE EXCEPTION 'only the acknowledgement of gate event % may change', OLD.id
      USING ERRCODE = 'restrict_violation', CONSTRAINT = 'gate_events_ack_once';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER gate_events_ack_once BEFORE UPDATE ON gate_events
  FOR EACH ROW EXECUTE FUNCTION gate_event_ack_once();

-- An allocation can be released exactly once, and nothing else may change.
CREATE FUNCTION ledger_allocation_release_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.released_at IS NOT NULL OR NEW.released_at IS NULL THEN
    RAISE EXCEPTION 'allocation % can only be released, and only once', OLD.id
      USING ERRCODE = 'restrict_violation', CONSTRAINT = 'ledger_allocations_release_once';
  END IF;
  IF (to_jsonb(NEW) - 'released_at' - 'released_by' - 'release_reason')
     IS DISTINCT FROM (to_jsonb(OLD) - 'released_at' - 'released_by' - 'release_reason') THEN
    RAISE EXCEPTION 'only the release columns of allocation % may change', OLD.id
      USING ERRCODE = 'restrict_violation', CONSTRAINT = 'ledger_allocations_release_once';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ledger_allocations_release_once BEFORE UPDATE ON ledger_allocations
  FOR EACH ROW EXECUTE FUNCTION ledger_allocation_release_once();

-- -----------------------------------------------------------------------------
-- 2. ledger_guard: checked as each ledger row is inserted
-- -----------------------------------------------------------------------------
-- Every money write for a student first locks that student's row. That makes
-- all ledger and allocation changes for one student happen one at a time, so
-- two concurrent requests can't both read "Rs 5,000 held" and both refund it.
-- (In READ COMMITTED, each statement in this function takes a fresh snapshot,
-- so the SUM below, run after the lock, sees the other transaction's rows.)
CREATE FUNCTION ledger_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  orig    ledger_entries;
  req     refund_requests;
  balance numeric(12,2);
BEGIN
  PERFORM 1 FROM students WHERE id = NEW.student_id FOR UPDATE;

  -- (a) A reversal is the exact opposite of one earlier, non-reversal entry.
  IF NEW.entry_type = 'reversal' THEN
    SELECT * INTO orig FROM ledger_entries WHERE id = NEW.reverses_entry_id;
    IF NOT FOUND OR orig.entry_type = 'reversal' OR NEW.amount <> -orig.amount
       OR NEW.account <> orig.account OR NEW.student_id <> orig.student_id THEN
      RAISE EXCEPTION 'invalid reversal of ledger entry %', NEW.reverses_entry_id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_guard_reversal';
    END IF;
  END IF;

  -- (b) Refund money moves only for an approved request, for exactly its amount.
  IF NEW.refund_request_id IS NOT NULL THEN
    SELECT * INTO req FROM refund_requests WHERE id = NEW.refund_request_id FOR UPDATE;
    IF NOT FOUND OR req.status <> 'approved' OR abs(NEW.amount) <> req.amount
       OR NEW.account <> req.account OR NEW.student_id <> req.student_id THEN
      RAISE EXCEPTION 'refund request % is not approved for this amount', NEW.refund_request_id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_guard_refund_not_approved';
    END IF;
  END IF;

  -- (c) Balance floors: deposit held never negative; a dues refund never exceeds the credit.
  IF (NEW.account = 'deposit' AND NEW.amount < 0) OR NEW.entry_type = 'refund' THEN
    SELECT coalesce(sum(amount), 0) INTO balance
      FROM ledger_entries WHERE student_id = NEW.student_id AND account = NEW.account;
    IF NEW.account = 'deposit' AND balance + NEW.amount < 0 THEN
      RAISE EXCEPTION 'deposit held would become negative (held %, change %)', balance, NEW.amount
        USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_guard_deposit_floor';
    ELSIF NEW.entry_type = 'refund' AND balance + NEW.amount > 0 THEN
      RAISE EXCEPTION 'refund exceeds the student''s credit balance'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_guard_refund_exceeds_credit';
    END IF;
  END IF;

  RETURN NEW;
END $$;
CREATE TRIGGER ledger_guard BEFORE INSERT ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION ledger_guard();

-- -----------------------------------------------------------------------------
-- 3. Deferred checks on the ledger (run at COMMIT)
-- -----------------------------------------------------------------------------
-- A payment's ledger entries add up to the payment, belong to the same student,
-- and every payment has entries. Attached to both tables so neither side can
-- be written alone.
CREATE FUNCTION check_payment_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  pid      uuid;
  p        payments;
  posted   numeric(12,2);
BEGIN
  IF TG_TABLE_NAME = 'payments' THEN pid := NEW.id; ELSE pid := NEW.payment_id; END IF;
  SELECT * INTO p FROM payments WHERE id = pid;
  SELECT coalesce(sum(abs(amount)), 0) INTO posted FROM ledger_entries WHERE payment_id = pid;
  IF posted <> p.amount THEN
    RAISE EXCEPTION 'payment % is % but its ledger entries total %', pid, p.amount, posted
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_payment_balanced';
  END IF;
  IF EXISTS (SELECT 1 FROM ledger_entries WHERE payment_id = pid AND student_id <> p.student_id) THEN
    RAISE EXCEPTION 'payment % has ledger entries for another student', pid
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_payment_balanced';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ledger_payment_balanced
  AFTER INSERT ON ledger_entries DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.payment_id IS NOT NULL) EXECUTE FUNCTION check_payment_balanced();
CREATE CONSTRAINT TRIGGER payments_balanced
  AFTER INSERT ON payments DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_payment_balanced();

-- A reversed payment has ALL its entries reversed, and a payment's entry is only
-- reversed together with a payment_reversals row.
CREATE FUNCTION check_payment_reversal_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE pid uuid;
BEGIN
  IF TG_TABLE_NAME = 'payment_reversals' THEN
    IF EXISTS (SELECT 1 FROM ledger_entries e
                WHERE e.payment_id = NEW.payment_id
                  AND NOT EXISTS (SELECT 1 FROM ledger_entries r WHERE r.reverses_entry_id = e.id)) THEN
      RAISE EXCEPTION 'payment % is marked reversed but not all its entries are reversed', NEW.payment_id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'payment_reversal_complete';
    END IF;
  ELSE
    SELECT payment_id INTO pid FROM ledger_entries WHERE id = NEW.reverses_entry_id;
    IF pid IS NOT NULL AND NOT EXISTS (SELECT 1 FROM payment_reversals WHERE payment_id = pid) THEN
      RAISE EXCEPTION 'an entry of payment % was reversed without reversing the payment', pid
        USING ERRCODE = 'check_violation', CONSTRAINT = 'payment_reversal_complete';
    END IF;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER payment_reversal_complete
  AFTER INSERT ON payment_reversals DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_payment_reversal_consistent();
CREATE CONSTRAINT TRIGGER ledger_payment_entry_reversal_complete
  AFTER INSERT ON ledger_entries DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.entry_type = 'reversal') EXECUTE FUNCTION check_payment_reversal_consistent();

-- Applying a deposit to dues moves the same amount out of both balances.
CREATE FUNCTION check_deposit_applied_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE dues numeric(12,2); dep numeric(12,2);
BEGIN
  SELECT coalesce(sum(amount) FILTER (WHERE account = 'dues'), 0),
         coalesce(sum(amount) FILTER (WHERE account = 'deposit'), 0)
    INTO dues, dep
    FROM ledger_entries
   WHERE txn_id = NEW.txn_id AND student_id = NEW.student_id AND entry_type = 'deposit_applied';
  IF dues <> dep OR dues = 0 THEN
    RAISE EXCEPTION 'deposit application in transaction % is unbalanced (dues %, deposit %)', NEW.txn_id, dues, dep
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_deposit_applied_balanced';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ledger_deposit_applied_balanced
  AFTER INSERT ON ledger_entries DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.entry_type = 'deposit_applied') EXECUTE FUNCTION check_deposit_applied_balanced();

-- -----------------------------------------------------------------------------
-- 4. Allocations (Q60, Q64)
-- -----------------------------------------------------------------------------
CREATE FUNCTION ledger_allocations_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  c    ledger_entries;
  d    ledger_entries;
  used numeric(12,2);
BEGIN
  PERFORM 1 FROM students WHERE id = NEW.student_id FOR UPDATE;   -- same lock as ledger_guard
  SELECT * INTO c FROM ledger_entries WHERE id = NEW.credit_entry_id;
  SELECT * INTO d FROM ledger_entries WHERE id = NEW.debit_entry_id;

  IF c.account <> 'dues' OR c.entry_type NOT IN ('payment', 'waiver', 'deposit_applied') OR c.amount >= 0 THEN
    RAISE EXCEPTION 'entry % cannot be the credit side of an allocation', c.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_allocations_guard';
  END IF;
  IF d.account <> 'dues' OR d.entry_type NOT IN ('charge', 'late_fee', 'fine', 'refund') OR d.amount <= 0 THEN
    RAISE EXCEPTION 'entry % cannot be the debit side of an allocation', d.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_allocations_guard';
  END IF;
  IF EXISTS (SELECT 1 FROM ledger_entries WHERE reverses_entry_id IN (c.id, d.id)) THEN
    RAISE EXCEPTION 'reversed entries cannot be allocated'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_allocations_guard';
  END IF;
  IF c.entry_type = 'waiver' AND NEW.method <> 'manual' THEN
    RAISE EXCEPTION 'a waiver is allocated to the charge chosen for it (method manual)'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_allocations_guard';
  END IF;

  SELECT coalesce(sum(amount), 0) INTO used
    FROM ledger_allocations WHERE credit_entry_id = c.id AND released_at IS NULL;
  IF used + NEW.amount > -c.amount THEN
    RAISE EXCEPTION 'allocation exceeds the unallocated part of credit %', c.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_allocations_exceeds_balance';
  END IF;
  SELECT coalesce(sum(amount), 0) INTO used
    FROM ledger_allocations WHERE debit_entry_id = d.id AND released_at IS NULL;
  IF used + NEW.amount > d.amount THEN
    RAISE EXCEPTION 'allocation exceeds the outstanding part of debit %', d.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_allocations_exceeds_balance';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ledger_allocations_guard BEFORE INSERT ON ledger_allocations
  FOR EACH ROW EXECUTE FUNCTION ledger_allocations_guard();

-- At commit: a reversed entry keeps no active allocations.
CREATE FUNCTION check_reversal_releases_allocations() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM ledger_allocations
              WHERE released_at IS NULL
                AND (credit_entry_id = NEW.reverses_entry_id OR debit_entry_id = NEW.reverses_entry_id)) THEN
    RAISE EXCEPTION 'entry % was reversed but still has active allocations', NEW.reverses_entry_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_reversal_releases_allocations';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ledger_reversal_releases_allocations
  AFTER INSERT ON ledger_entries DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.entry_type = 'reversal') EXECUTE FUNCTION check_reversal_releases_allocations();

-- At commit: a waiver is fully allocated, manually, to exactly one charge (Q64).
CREATE FUNCTION check_waiver_fully_allocated() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE total numeric(12,2); targets int;
BEGIN
  SELECT coalesce(sum(amount), 0), count(DISTINCT debit_entry_id) INTO total, targets
    FROM ledger_allocations
   WHERE credit_entry_id = NEW.id AND released_at IS NULL AND method = 'manual';
  IF total <> -NEW.amount OR targets <> 1 THEN
    RAISE EXCEPTION 'waiver % must be fully allocated to exactly one charge', NEW.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_waiver_fully_allocated';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ledger_waiver_fully_allocated
  AFTER INSERT ON ledger_entries DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.entry_type = 'waiver') EXECUTE FUNCTION check_waiver_fully_allocated();

-- -----------------------------------------------------------------------------
-- 5. Inventory: quantity_on_hand follows stock_movements
-- -----------------------------------------------------------------------------
-- SECURITY DEFINER: runs with the owner's privileges, because hms_app may not
-- UPDATE quantity_on_hand directly (column grants, 50_grants.sql). RLS still
-- applies (FORCE) and uses the caller's app.org_id. The UPDATE's row lock
-- serializes concurrent movements; the CHECK (quantity_on_hand >= 0) rejects a
-- deduction that would make stock negative.
CREATE FUNCTION apply_stock_movement() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  UPDATE inventory_items
     SET quantity_on_hand = quantity_on_hand + NEW.quantity, updated_at = now()
   WHERE id = NEW.item_id AND org_id = NEW.org_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'inventory item % not found', NEW.item_id USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER stock_movements_apply AFTER INSERT ON stock_movements
  FOR EACH ROW EXECUTE FUNCTION apply_stock_movement();
