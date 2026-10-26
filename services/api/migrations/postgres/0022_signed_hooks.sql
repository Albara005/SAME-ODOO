-- 0022: an inbound webhook can require signed calls (the signing secret, encrypted).
ALTER TABLE workflows ADD COLUMN hook_signing_secret TEXT;
