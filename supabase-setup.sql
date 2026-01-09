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
CREATE POLICY "Allow all operations" ON prospects FOR ALL USING (true);
CREATE POLICY "Allow all operations" ON daily_planning FOR ALL USING (true);
CREATE POLICY "Allow all operations" ON tasks FOR ALL USING (true);
