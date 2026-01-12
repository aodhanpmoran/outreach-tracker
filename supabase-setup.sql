-- Run this in Supabase SQL Editor to create the required tables

-- Prospects table
CREATE TABLE IF NOT EXISTS prospects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    company TEXT,
    email TEXT,
    linkedin TEXT,
    notes TEXT,
    status TEXT DEFAULT 'new',
    next_followup DATE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Daily planning table
CREATE TABLE IF NOT EXISTS daily_planning (
    id SERIAL PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    one_thing TEXT,
    tasks JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Tasks table (long-term tracking of all tasks)
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP WITH TIME ZONE,
    date_entered DATE NOT NULL DEFAULT CURRENT_DATE,
    date_scheduled DATE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for querying tasks by date
CREATE INDEX IF NOT EXISTS idx_tasks_date_entered ON tasks(date_entered);
CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks(completed);

-- Enable Row Level Security (optional but recommended)
ALTER TABLE prospects ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_planning ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;

-- Create policies to allow all operations (adjust as needed)
DROP POLICY IF EXISTS "Allow all operations" ON prospects;
CREATE POLICY "Allow all operations" ON prospects FOR ALL USING (true);
DROP POLICY IF EXISTS "Allow all operations" ON daily_planning;
CREATE POLICY "Allow all operations" ON daily_planning FOR ALL USING (true);
DROP POLICY IF EXISTS "Allow all operations" ON tasks;
CREATE POLICY "Allow all operations" ON tasks FOR ALL USING (true);

-- =====================================================
-- FATHOM INTEGRATION TABLES
-- =====================================================

-- Add llm_created column to prospects table (for auto-created contacts)
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS llm_created BOOLEAN DEFAULT FALSE;

-- Fathom calls table - stores meeting records from Fathom.ai
CREATE TABLE IF NOT EXISTS fathom_calls (
    id SERIAL PRIMARY KEY,
    fathom_recording_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    transcript_url TEXT,
    call_date TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_minutes INTEGER,
    recorded_by_email TEXT,
    prospect_id INTEGER REFERENCES prospects(id) ON DELETE SET NULL,
    auto_matched BOOLEAN DEFAULT FALSE,
    match_confidence TEXT,  -- 'high', 'medium', 'low', 'manual'
    needs_review BOOLEAN DEFAULT FALSE,
    llm_extraction JSONB,  -- Stores OpenAI response for audit
    raw_data JSONB,  -- Store full Fathom response for reference
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fathom_calls_prospect_id ON fathom_calls(prospect_id);
CREATE INDEX IF NOT EXISTS idx_fathom_calls_call_date ON fathom_calls(call_date);
CREATE INDEX IF NOT EXISTS idx_fathom_calls_recording_id ON fathom_calls(fathom_recording_id);
CREATE INDEX IF NOT EXISTS idx_fathom_calls_needs_review ON fathom_calls(needs_review);

ALTER TABLE fathom_calls ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all operations" ON fathom_calls;
CREATE POLICY "Allow all operations" ON fathom_calls FOR ALL USING (true);

-- Fathom action items table - stores action items from calls
CREATE TABLE IF NOT EXISTS fathom_action_items (
    id SERIAL PRIMARY KEY,
    fathom_call_id INTEGER NOT NULL REFERENCES fathom_calls(id) ON DELETE CASCADE,
    fathom_item_id TEXT,  -- Fathom's internal ID if available
    description TEXT NOT NULL,
    assignee TEXT,
    completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP WITH TIME ZONE,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,  -- Link to daily planning task
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fathom_action_items_call_id ON fathom_action_items(fathom_call_id);
CREATE INDEX IF NOT EXISTS idx_fathom_action_items_task_id ON fathom_action_items(task_id);

ALTER TABLE fathom_action_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all operations" ON fathom_action_items;
CREATE POLICY "Allow all operations" ON fathom_action_items FOR ALL USING (true);

-- Fathom sync log table - tracks sync operations for debugging
CREATE TABLE IF NOT EXISTS fathom_sync_log (
    id SERIAL PRIMARY KEY,
    sync_type TEXT NOT NULL,  -- 'cron_hourly', 'telegram_manual', 'api_manual'
    status TEXT NOT NULL,  -- 'started', 'completed', 'failed'
    meetings_processed INTEGER DEFAULT 0,
    meetings_new INTEGER DEFAULT 0,
    contacts_created INTEGER DEFAULT 0,
    needs_review_count INTEGER DEFAULT 0,
    errors JSONB,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

ALTER TABLE fathom_sync_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all operations" ON fathom_sync_log;
CREATE POLICY "Allow all operations" ON fathom_sync_log FOR ALL USING (true);
