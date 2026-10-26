-- 0052: vendor bills (and the purchase orders they come from) in other currencies, as invoices are since 0035
-- (novara/modules/purchasing/service.py). The ledger stays in the organization's currency: bills keep their exchange
-- rate, the amounts they were booked at and what they took out of goods received not invoiced, payments their own
-- rate and exchange difference, vendor credits and refunds the booked amounts at the bill's rate. Quotations, sales
-- orders and purchase orders only needed their currency, which they already had. Existing documents are all in the
-- organization's currency: their booked amounts are their amounts.

ALTER TABLE vendor_bills ADD COLUMN exchange_rate NUMERIC(18, 8) DEFAULT 1 NOT NULL;
ALTER TABLE vendor_bills ADD COLUMN base_subtotal_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE vendor_bills ADD COLUMN base_total_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE vendor_bills ADD COLUMN base_settled_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE vendor_bills ADD COLUMN grni_base JSON DEFAULT '{}' NOT NULL;
ALTER TABLE bill_payments ADD COLUMN exchange_rate NUMERIC(18, 8) DEFAULT 1 NOT NULL;
ALTER TABLE bill_payments ADD COLUMN base_amount_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE bill_payments ADD COLUMN fx_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE vendor_credits ADD COLUMN base_total_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE vendor_credits ADD COLUMN base_applied_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE vendor_credits ADD COLUMN base_refunded_minor BIGINT DEFAULT 0 NOT NULL;
ALTER TABLE vendor_refunds ADD COLUMN base_amount_minor BIGINT DEFAULT 0 NOT NULL;

-- (Before the trigger below: 0041's version doesn't look at the new columns.)
UPDATE vendor_bills SET base_subtotal_minor = subtotal_minor, base_total_minor = total_minor,
                        base_settled_minor = amount_paid_minor + amount_credited_minor
 WHERE status <> 'draft';
UPDATE bill_payments SET base_amount_minor = amount_minor;
UPDATE vendor_credits SET base_total_minor = total_minor, base_applied_minor = applied_minor,
                          base_refunded_minor = refunded_minor;
UPDATE vendor_refunds SET base_amount_minor = amount_minor;

-- A posted bill's rate and booked amounts are as final as the rest of it (0041's rule, extended).
CREATE OR REPLACE FUNCTION vendor_bill_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'posted bill % cannot be deleted', OLD.number USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'draft' AND (
        NEW.vendor_id, NEW.vendor_reference, NEW.bill_date, NEW.due_date, NEW.currency, NEW.notes,
        NEW.subtotal_minor, NEW.tax_minor, NEW.total_minor, NEW.number, NEW.journal_entry_id,
        NEW.exchange_rate, NEW.base_subtotal_minor, NEW.base_total_minor, NEW.grni_base::text
    ) IS DISTINCT FROM (
        OLD.vendor_id, OLD.vendor_reference, OLD.bill_date, OLD.due_date, OLD.currency, OLD.notes,
        OLD.subtotal_minor, OLD.tax_minor, OLD.total_minor, OLD.number, OLD.journal_entry_id,
        OLD.exchange_rate, OLD.base_subtotal_minor, OLD.base_total_minor, OLD.grni_base::text
    ) THEN
        RAISE EXCEPTION 'posted bill % cannot be edited', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status <> 'draft' AND NEW.status = 'draft' THEN
        RAISE EXCEPTION 'posted bill % cannot go back to draft', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'cancelled' AND NEW.status <> 'cancelled' THEN
        RAISE EXCEPTION 'cancelled bill % cannot be reopened', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
