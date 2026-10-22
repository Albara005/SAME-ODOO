-- 0005: isolation, privileges and accounting integrity for the business tables.

-- Row-level security: same policy as the platform tables (see 0002).
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['accounts', 'taxes', 'number_sequences', 'journal_entries', 'journal_lines',
                             'customers', 'products', 'invoices', 'invoice_lines', 'payments']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (organization_id = app_current_org())'
                       ' WITH CHECK (organization_id = app_current_org())', t);
    END LOOP;
END $$;

-- Every journal entry must balance and have at least two lines. Checked at COMMIT
-- (deferred), so an entry and its lines can be inserted in any order within a transaction.
CREATE FUNCTION journal_entry_must_balance() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    -- TG_ARGV[0] names the column holding the entry id ('id' or 'entry_id').
    target text := to_jsonb(NEW) ->> TG_ARGV[0];
    debit bigint;
    credit bigint;
    n int;
BEGIN
    SELECT coalesce(sum(debit_minor), 0), coalesce(sum(credit_minor), 0), count(*)
      INTO debit, credit, n FROM journal_lines WHERE entry_id = target;
    IF n < 2 OR debit <> credit THEN
        RAISE EXCEPTION 'journal entry % is unbalanced (debit %, credit %, % lines)', target, debit, credit, n
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER journal_entry_balanced AFTER INSERT ON journal_entries
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION journal_entry_must_balance('id');
CREATE CONSTRAINT TRIGGER journal_line_balanced AFTER INSERT ON journal_lines
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION journal_entry_must_balance('entry_id');

-- Posted invoices are final: their accounting fields and lines cannot change.
CREATE FUNCTION invoice_is_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'posted invoice % cannot be deleted', OLD.number USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'draft' AND (
        NEW.customer_id, NEW.issue_date, NEW.due_date, NEW.currency, NEW.notes,
        NEW.subtotal_minor, NEW.tax_minor, NEW.total_minor, NEW.number, NEW.journal_entry_id
    ) IS DISTINCT FROM (
        OLD.customer_id, OLD.issue_date, OLD.due_date, OLD.currency, OLD.notes,
        OLD.subtotal_minor, OLD.tax_minor, OLD.total_minor, OLD.number, OLD.journal_entry_id
    ) THEN
        RAISE EXCEPTION 'posted invoice % cannot be edited', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'cancelled' AND NEW.status <> 'cancelled' THEN
        RAISE EXCEPTION 'cancelled invoice % cannot be reopened', OLD.number USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER invoice_final BEFORE UPDATE OR DELETE ON invoices
    FOR EACH ROW EXECUTE FUNCTION invoice_is_final();

CREATE FUNCTION invoice_line_requires_draft() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_status text;
BEGIN
    SELECT status INTO parent_status FROM invoices
     WHERE id = CASE TG_OP WHEN 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;
    IF parent_status IS NOT NULL AND parent_status <> 'draft' THEN
        RAISE EXCEPTION 'lines of a posted invoice cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER invoice_line_draft_only BEFORE INSERT OR UPDATE OR DELETE ON invoice_lines
    FOR EACH ROW EXECUTE FUNCTION invoice_line_requires_draft();

-- Runtime role privileges. The ledger and payments are append-only for the API.
DO $$
DECLARE app_role text := current_setting('novara.app_role', true);
BEGIN
    IF app_role IS NULL OR app_role = '' THEN
        RAISE NOTICE 'novara.app_role not set; skipping grants';
        RETURN;
    END IF;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON accounts, taxes, customers, products, invoices, invoice_lines TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON number_sequences TO %I', app_role);
    EXECUTE format('GRANT SELECT, INSERT ON journal_entries, journal_lines, payments TO %I', app_role);
END $$;
