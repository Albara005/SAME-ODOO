-- 0046: HyperPay as an online payment provider (novara/payments/providers.py): COPYandPAY with mada, Visa and
-- Mastercard. The access token and the webhook's decryption key are stored encrypted, like the other providers'
-- secrets; the entity ids aren't secret.

ALTER TABLE payment_settings DROP CONSTRAINT ck_payment_settings_provider;
ALTER TABLE payment_settings ADD CONSTRAINT ck_payment_settings_provider
    CHECK (provider IN ('none', 'stripe', 'moyasar', 'hyperpay', 'test'));
ALTER TABLE payment_settings ADD COLUMN hyperpay_access_token TEXT;
ALTER TABLE payment_settings ADD COLUMN hyperpay_token_hint VARCHAR(16) DEFAULT '' NOT NULL;
ALTER TABLE payment_settings ADD COLUMN hyperpay_entity_id VARCHAR(64) DEFAULT '' NOT NULL;
ALTER TABLE payment_settings ADD COLUMN hyperpay_mada_entity_id VARCHAR(64) DEFAULT '' NOT NULL;
ALTER TABLE payment_settings ADD COLUMN hyperpay_live BOOLEAN DEFAULT false NOT NULL;
ALTER TABLE payment_settings ADD COLUMN hyperpay_webhook_key TEXT;
