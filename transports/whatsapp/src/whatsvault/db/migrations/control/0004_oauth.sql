-- OAuth 2.1 authorization server state, for the public-connector deployment.
--
-- Only needed when the MCP surface is reachable from outside this machine. On
-- loopback the static bearer token remains sufficient; these tables stay empty.
--
-- Nothing here stores a credential in recoverable form. Authorization codes and
-- both token kinds are held as SHA-256 hashes: the control database is
-- encrypted, but a token readable inside it is one that leaks through any backup
-- or diagnostic dump.

CREATE TABLE oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_name TEXT,
    -- JSON array. Matching is exact and https-only; prefix matching here is an
    -- open redirect.
    redirect_uris TEXT NOT NULL,
    created_ms INTEGER NOT NULL
);

-- An authorization request waiting for the operator to approve it from the
-- terminal. The consent page never accepts a secret: a public form asking for a
-- password is a phishing and brute-force target, and approval in this project
-- belongs on a channel the requester cannot reach.
CREATE TABLE oauth_pending (
    request_id TEXT PRIMARY KEY,
    user_code TEXT NOT NULL UNIQUE,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    redirect_uri TEXT NOT NULL,
    state TEXT,
    code_challenge TEXT NOT NULL,          -- S256 only; `plain` is refused
    scope TEXT NOT NULL,
    created_ms INTEGER NOT NULL,
    expires_ms INTEGER NOT NULL,
    approved_ms INTEGER,
    -- set once poll() has handed the code over, so one approval yields one code
    collected_ms INTEGER
);

CREATE TABLE oauth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    scope TEXT NOT NULL,
    issued_ms INTEGER NOT NULL,
    expires_ms INTEGER NOT NULL,
    consumed_ms INTEGER
);

CREATE TABLE oauth_tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('access','refresh')),
    client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
    -- CHECK, not convention: the read-only surface is the property the whole
    -- design protects, so a wider scope cannot be written even by a bug.
    scope TEXT NOT NULL CHECK (scope = 'whatsvault.read'),
    issued_ms INTEGER NOT NULL,
    expires_ms INTEGER NOT NULL,
    revoked_ms INTEGER
);

CREATE INDEX idx_oauth_tokens_client ON oauth_tokens(client_id);
CREATE INDEX idx_oauth_pending_expiry ON oauth_pending(expires_ms);
