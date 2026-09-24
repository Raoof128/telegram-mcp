-- Phase 2a: hard server-side MCP privacy fence (ledger #23).
-- Set only by the CLI/phone, never by an MCP tool. The read layer never returns
-- a LOCAL_ONLY conversation regardless of model intent.
ALTER TABLE conversations ADD COLUMN mcp_visibility TEXT NOT NULL DEFAULT 'ALLOW_MCP'
    CHECK (mcp_visibility IN ('ALLOW_MCP','LOCAL_ONLY'));
