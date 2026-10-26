-- 0040: Moyasar as an online payment provider (novara/payments/providers.py): mada, Apple Pay, STC Pay and cards in
-- Saudi Arabia. Its secret key and the webhook's secret token are stored encrypted, like Stripe's.

ALTER TABLE payment_settings DROP CONSTRAINT ck_payment_settings_provider;
ALTER TABLE payment_settings ADD CONSTRAINT ck_payment_settings_provider
    CHECK (provider IN ('none', 'stripe', 'moyasar', 'test'));
ALTER TABLE payment_settings ADD COLUMN moyasar_secret_key TEXT;
ALTER TABLE payment_settings ADD COLUMN moyasar_key_hint VARCHAR(16) DEFAULT '' NOT NULL;
ALTER TABLE payment_settings ADD COLUMN moyasar_webhook_secret TEXT;
