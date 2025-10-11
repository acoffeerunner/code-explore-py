-- =============================================================================
-- Code Explorer - Supabase Database Schema
-- =============================================================================
-- Run this in the Supabase SQL Editor to set up the database schema.
-- This creates all tables, indexes, constraints, and Row Level Security policies.

-- =============================================================================
-- TABLES
-- =============================================================================

-- repos table (user-owned repositories)
CREATE TABLE IF NOT EXISTS public.repos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    branch TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    progress_pct INTEGER DEFAULT 0,
    branches_available TEXT[],
    error_message TEXT,
    webhook_url TEXT,
    webhook_secret TEXT,  -- HMAC secret for webhook signatures - NEVER LOG THIS
    file_count INTEGER,
    chunk_count INTEGER,
    active_version_id UUID,  -- Current active index version
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    indexed_at TIMESTAMPTZ,

    CONSTRAINT valid_status CHECK (status IN ('pending', 'cloning', 'indexing', 'ready', 'failed')),
    CONSTRAINT unique_user_repo_branch UNIQUE (user_id, url, branch)
);

-- Indexes for repos
CREATE INDEX IF NOT EXISTS idx_repos_user ON public.repos(user_id);
CREATE INDEX IF NOT EXISTS idx_repos_status ON public.repos(status);
CREATE INDEX IF NOT EXISTS idx_repos_url_branch ON public.repos(url, branch);

-- index_versions table (versioned namespaces for Pinecone)
CREATE TABLE IF NOT EXISTS public.index_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id UUID NOT NULL REFERENCES public.repos(id) ON DELETE CASCADE,
    commit_hash VARCHAR(40) NOT NULL,
    pinecone_namespace TEXT NOT NULL,  -- Format: {repo_id}:{commit_hash}
    status VARCHAR(20) NOT NULL DEFAULT 'building',
    chunk_count INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    -- Enforce idempotency: same repo + commit = single version
    CONSTRAINT unique_repo_commit UNIQUE (repo_id, commit_hash),
    CONSTRAINT valid_version_status CHECK (status IN ('building', 'active', 'stale', 'deleted'))
);

-- Indexes for index_versions
CREATE INDEX IF NOT EXISTS idx_versions_repo ON public.index_versions(repo_id);
CREATE INDEX IF NOT EXISTS idx_versions_status ON public.index_versions(status);

-- chunks table (source-of-truth for chunk content)
CREATE TABLE IF NOT EXISTS public.chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES public.index_versions(id) ON DELETE CASCADE,
    repo_id UUID NOT NULL REFERENCES public.repos(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    symbol_name TEXT,
    symbol_type VARCHAR(50),  -- function, class, method, interface
    language VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,  -- Full chunk text stored here
    content_hash VARCHAR(64) NOT NULL,  -- SHA256 of content
    token_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT unique_chunk_location UNIQUE (version_id, file_path, start_line, end_line)
);

-- Indexes for chunks
CREATE INDEX IF NOT EXISTS idx_chunks_version ON public.chunks(version_id);
CREATE INDEX IF NOT EXISTS idx_chunks_repo ON public.chunks(repo_id);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON public.chunks(content_hash);

-- webhook_deliveries table (for tracking delivery attempts)
CREATE TABLE IF NOT EXISTS public.webhook_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id UUID NOT NULL REFERENCES public.repos(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,  -- "repo.indexed", "repo.failed"
    idempotency_key TEXT NOT NULL UNIQUE,  -- Format: {repo_id}:{status}:{commit_hash}
    payload JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    next_retry_at TIMESTAMPTZ,
    response_status INTEGER,
    response_body TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT valid_delivery_status CHECK (status IN ('pending', 'delivered', 'failed', 'exhausted'))
);

-- Indexes for webhook_deliveries
CREATE INDEX IF NOT EXISTS idx_deliveries_status ON public.webhook_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_deliveries_next_retry ON public.webhook_deliveries(next_retry_at)
    WHERE status = 'pending';

-- =============================================================================
-- AUTO-UPDATE TIMESTAMP TRIGGER
-- =============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for repos.updated_at
DROP TRIGGER IF EXISTS update_repos_updated_at ON public.repos;
CREATE TRIGGER update_repos_updated_at
    BEFORE UPDATE ON public.repos
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at_column();

-- =============================================================================
-- ROW LEVEL SECURITY (RLS) POLICIES
-- =============================================================================

-- Enable RLS on all tables
ALTER TABLE public.repos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.index_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.webhook_deliveries ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for idempotency)
DROP POLICY IF EXISTS "Users can view own repos" ON public.repos;
DROP POLICY IF EXISTS "Users can insert own repos" ON public.repos;
DROP POLICY IF EXISTS "Users can update own repos" ON public.repos;
DROP POLICY IF EXISTS "Users can delete own repos" ON public.repos;
DROP POLICY IF EXISTS "Users can access own index_versions" ON public.index_versions;
DROP POLICY IF EXISTS "Users can access own chunks" ON public.chunks;
DROP POLICY IF EXISTS "Users can access own webhook_deliveries" ON public.webhook_deliveries;

-- Policies for repos table
CREATE POLICY "Users can view own repos" ON public.repos
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own repos" ON public.repos
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own repos" ON public.repos
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own repos" ON public.repos
    FOR DELETE USING (auth.uid() = user_id);

-- Policies for index_versions (cascade from repo ownership)
CREATE POLICY "Users can access own index_versions" ON public.index_versions
    FOR ALL USING (
        repo_id IN (SELECT id FROM public.repos WHERE user_id = auth.uid())
    );

-- Policies for chunks (cascade from repo ownership)
CREATE POLICY "Users can access own chunks" ON public.chunks
    FOR ALL USING (
        repo_id IN (SELECT id FROM public.repos WHERE user_id = auth.uid())
    );

-- Policies for webhook_deliveries (cascade from repo ownership)
CREATE POLICY "Users can access own webhook_deliveries" ON public.webhook_deliveries
    FOR ALL USING (
        repo_id IN (SELECT id FROM public.repos WHERE user_id = auth.uid())
    );

-- =============================================================================
-- SERVICE ROLE BYPASS
-- =============================================================================
-- The service role key bypasses RLS, allowing backend workers to:
-- - Update repo status during indexing
-- - Insert chunks from the worker process
-- - Manage webhook deliveries
-- This is the intended design - authenticated users see only their data,
-- while the backend service has full access.

-- =============================================================================
-- GRANTS
-- =============================================================================

-- Grant usage on public schema
GRANT USAGE ON SCHEMA public TO anon, authenticated;

-- Grant table access to authenticated users (RLS will filter)
GRANT SELECT, INSERT, UPDATE, DELETE ON public.repos TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.index_versions TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.chunks TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.webhook_deliveries TO authenticated;

-- Grant sequence usage for UUID generation
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO authenticated;
