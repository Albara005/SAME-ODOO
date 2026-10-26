-- 0041: debit notes (novara/modules/invoicing): an invoice that charges more on an earlier one, numbered DN- and
-- e-invoiced as type 383 referring to it. And a posted invoice or vendor bill can't go back to draft: until now
-- only the application refused that.

ALTER TABLE invoices ADD COLUMN debit_note_of_id VARCHAR(36);
ALTER TABLE invoices ADD COLUMN reason TEXT DEFAULT '' NOT NULL;
ALTER TABLE invoices ADD CONSTRAINT invoices_organization_id_debit_note_of_id_fkey
    FOREIGN KEY (organization_id, debit_note_of_id) REFERENCES invoices (organization_id, id);
CREATE INDEX ix_invoices_debit_note_of_id ON invoices (debit_note_of_id);

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
        NEW.exchange_rate, NEW.base_subtotal_minor, NEW.base_total_minor, NEW.debit_note_of_id, NEW.reason
    ) IS DISTINCT FROM (
        OLD.customer_id, OLD.issue_date, OLD.due_date, OLD.currency, OLD.notes,
        OLD.subtotal_minor, OLD.tax_minor, OLD.total_minor, OLD.number, OLD.journal_entry_id,
        OLD.exchange_rate, OLD.base_subtotal_minor, OLD.base_total_minor, OLD.debit_note_of_id, OLD.reason
    ) THEN
        RAISE EXCEPTION 'posted invoice % cannot be edited', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status <> 'draft' AND NEW.status = 'draft' THEN
        RAISE EXCEPTION 'posted invoice % cannot go back to draft', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'cancelled' AND NEW.status <> 'cancelled' THEN
        RAISE EXCEPTION 'cancelled invoice % cannot be reopened', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

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
        NEW.subtotal_minor, NEW.tax_minor, NEW.total_minor, NEW.number, NEW.journal_entry_id
    ) IS DISTINCT FROM (
        OLD.vendor_id, OLD.vendor_reference, OLD.bill_date, OLD.due_date, OLD.currency, OLD.notes,
        OLD.subtotal_minor, OLD.tax_minor, OLD.total_minor, OLD.number, OLD.journal_entry_id
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
