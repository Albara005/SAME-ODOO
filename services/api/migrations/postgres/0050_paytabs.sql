-- 0050: PayTabs as an online payment provider (novara/payments/providers.py): its hosted payment page, made when the
-- customer opens NOVARA's link. The profile id isn't secret; the server key is stored encrypted like the other
-- providers' secrets (it also signs PayTabs' calls back). The region decides PayTabs' API address.

ALTER TABLE payment_settings DROP CONSTRAINT ck_payment_settings_provider;
ALTER TABLE payment_settings ADD CONSTRAINT ck_payment_settings_provider
    CHECK (provider IN ('none', 'stripe', 'moyasar', 'hyperpay', 'paytabs', 'test'));
ALTER TABLE payment_settings ADD COLUMN paytabs_profile_id VARCHAR(20) DEFAULT '' NOT NULL;
ALTER TABLE payment_settings ADD COLUMN paytabs_server_key TEXT;
ALTER TABLE payment_settings ADD COLUMN paytabs_key_hint VARCHAR(16) DEFAULT '' NOT NULL;
ALTER TABLE payment_settings ADD COLUMN paytabs_region VARCHAR(8) DEFAULT 'sa' NOT NULL;
ALTER TABLE payment_settings ADD CONSTRAINT ck_payment_settings_paytabs_region
    CHECK (paytabs_region IN ('sa', 'ae', 'eg', 'om', 'jo', 'kw', 'iq', 'ma', 'qa', 'global'));
