-- 0025: a session remembers whether it was opened with a second step (an app code or a passkey),
-- so a passkey can meet an organization's two-step requirement for the session that used it.
-- Sessions from before this migration count as one step; accounts with the app on still pass as before.
ALTER TABLE user_sessions ADD COLUMN two_step BOOLEAN DEFAULT false NOT NULL;
