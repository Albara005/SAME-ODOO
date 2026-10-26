-- 0051: refunds through the payment provider (novara/payments/service.py refund_online). A credit note on an invoice
-- paid online is paid back through the same link's payment, out of Online Payments Clearing; the refund keeps the
-- link and the provider's id for it. HyperPay refunds go through the entity the payment was made with (mada or
-- card), which the link now keeps.

ALTER TABLE payment_links ADD UNIQUE (organization_id, id);
ALTER TABLE payment_links ADD COLUMN provider_detail VARCHAR(16) DEFAULT '' NOT NULL;

ALTER TABLE refunds ADD COLUMN payment_link_id VARCHAR(36);
ALTER TABLE refunds ADD COLUMN provider_reference VARCHAR(255) DEFAULT '' NOT NULL;
ALTER TABLE refunds ADD FOREIGN KEY(organization_id, payment_link_id) REFERENCES payment_links (organization_id, id);
CREATE INDEX ix_refunds_payment_link_id ON refunds (payment_link_id);
