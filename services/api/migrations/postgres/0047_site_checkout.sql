-- 0047: paying on the published site (sales/web_orders.py, payments/service.py). A site with the "checkout" scope
-- sends the visitor to a payment link for the order itself; when the provider confirms the money, the order becomes
-- the customer's posted, paid invoice.

ALTER TABLE payment_links ALTER COLUMN invoice_id DROP NOT NULL;
ALTER TABLE payment_links ADD COLUMN web_order_id VARCHAR(36);
ALTER TABLE payment_links ADD FOREIGN KEY (organization_id, web_order_id) REFERENCES web_orders (organization_id, id);
CREATE INDEX ix_payment_links_web_order_id ON payment_links (web_order_id);
-- A link is for an invoice or for a site's order, never both.
ALTER TABLE payment_links ADD CONSTRAINT ck_payment_link_subject CHECK ((invoice_id IS NULL) <> (web_order_id IS NULL));

ALTER TABLE web_orders ADD COLUMN invoice_id VARCHAR(36);
ALTER TABLE web_orders ADD FOREIGN KEY (organization_id, invoice_id) REFERENCES invoices (organization_id, id);
