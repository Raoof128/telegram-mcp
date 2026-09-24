-- Ties every token to the grant it descends from, so that reuse of a rotated
-- refresh token can revoke the whole family.
--
-- OAuth 2.1 treats reuse of a rotated refresh token as evidence that it was
-- captured. Refusing only the one call leaves the thief's freshly rotated pair
-- alive; revoking the family is the response the spec asks for, and it needs a
-- family to revoke.
ALTER TABLE oauth_tokens ADD COLUMN grant_id TEXT;
CREATE INDEX idx_oauth_tokens_grant ON oauth_tokens(grant_id);
