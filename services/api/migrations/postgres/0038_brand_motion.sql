-- 0038: how a brand moves (novara/creative/motion.py): a motion personality and speed per brand kit. Its tokens,
-- animations and scroll reveal are generated from these two values, like the colour system from the colours.

ALTER TABLE brand_kits ADD COLUMN motion JSON DEFAULT '{"personality": "calm", "speed": 1.0}' NOT NULL;
