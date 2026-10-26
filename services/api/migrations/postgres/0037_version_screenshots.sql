-- 0037: App Studio browser checks (novara/studio/browser.py) keep screenshots of each version's home page on a
-- desktop and a phone screen, as content-addressed blobs. Versions stay immutable: set once, when cut.

ALTER TABLE app_versions ADD COLUMN screenshots JSON DEFAULT '{}' NOT NULL;
