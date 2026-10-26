-- 0035: invoices in other currencies (novara/modules/invoicing/service.py). The ledger stays in the
-- organization's currency: invoices keep their exchange rate and the amounts they were booked at, payments
-- their own rate and exchange difference, credit notes and refunds the booked amounts at the invoice's rate.
-- Existing documents are all in the organization's currency: their booked amounts are their amounts.

ALTER TABLE invoices ADD COLUMN exchange_rate NUMERIC(18, 8) DEFAULT 1 NOT NULL;
ALTER TABLE invoices ADD COLUMN base_subtotal_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE invoices ADD COLUMN base_total_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE invoices ADD COLUMN base_settled_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE payments ADD COLUMN exchange_rate NUMERIC(18, 8) DEFAULT 1 NOT NULL;
ALTER TABLE payments ADD COLUMN base_amount_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE payments ADD COLUMN fx_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE credit_notes ADD COLUMN base_total_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE credit_notes ADD COLUMN base_applied_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE credit_notes ADD COLUMN base_refunded_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE refunds ADD COLUMN base_amount_minor BIGINT DEFAULT 0 NOT NULL;

UPDATE invoices SET base_subtotal_minor = subtotal_minor, base_total_minor = total_minor,
                    base_settled_minor = amount_paid_minor + amount_credited_minor
 WHERE status <> 'draft';
UPDATE payments SET base_amount_minor = amount_minor;
UPDATE credit_notes SET base_total_minor = total_minor, base_applied_minor = applied_minor,
                        base_refunded_minor = refunded_minor;
UPDATE refunds SET base_amount_minor = amount_minor;

-- A posted invoice's rate and booked amounts are as final as the rest of it (0005's rule, extended).
CREATE OR REPLACE FUNCTION invoice_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'posted invoice % cannot be deleted', OLD.number USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'draft' AND (
        NEW.customer_id, NEW.issue_date, NEW.due_date, NEW.currency, NEW.notes,
        NEW.subtotal_minor, NEW.tax_minor, NEW.total_minor, NEW.number, NEW.journal_entry_id,
        NEW.exchange_rate, NEW.base_subtotal_minor, NEW.base_total_minor
    ) IS DISTINCT FROM (
        OLD.customer_id, OLD.issue_date, OLD.due_date, OLD.currency, OLD.notes,
        OLD.subtotal_minor, OLD.tax_minor, OLD.total_minor, OLD.number, OLD.journal_entry_id,
        OLD.exchange_rate, OLD.base_subtotal_minor, OLD.base_total_minor
    ) THEN
        RAISE EXCEPTION 'posted invoice % cannot be edited', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'cancelled' AND NEW.status <> 'cancelled' THEN
        RAISE EXCEPTION 'cancelled invoice % cannot be reopened', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
