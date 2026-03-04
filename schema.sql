-- Romanian Rebus Generator - Database Schema
-- Run this in Supabase SQL Editor (same instance as propozitii-nostime)

CREATE TABLE crossword_puzzles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(100),
  theme VARCHAR(200),
  grid_size SMALLINT NOT NULL DEFAULT 10,
  grid_template TEXT NOT NULL,
  grid_solution TEXT NOT NULL,
  difficulty SMALLINT DEFAULT 3,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE crossword_clues (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  puzzle_id UUID NOT NULL REFERENCES crossword_puzzles(id) ON DELETE CASCADE,
  direction CHAR(1) NOT NULL CHECK (direction IN ('H', 'V')),
  start_row SMALLINT NOT NULL,
  start_col SMALLINT NOT NULL,
  length SMALLINT NOT NULL,
  word_normalized VARCHAR(50) NOT NULL,
  word_original VARCHAR(50) NOT NULL,
  clue_number SMALLINT NOT NULL,
  definition TEXT NOT NULL,
  UNIQUE (puzzle_id, direction, start_row, start_col)
);

CREATE INDEX idx_clues_puzzle ON crossword_clues(puzzle_id);
CREATE INDEX idx_puzzles_published ON crossword_puzzles(published) WHERE published = TRUE;

-- Row-Level Security
ALTER TABLE crossword_puzzles ENABLE ROW LEVEL SECURITY;
ALTER TABLE crossword_clues ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read published" ON crossword_puzzles
  FOR SELECT USING (published = TRUE);

CREATE POLICY "Public read clues of published puzzles" ON crossword_clues
  FOR SELECT USING (
    puzzle_id IN (SELECT id FROM crossword_puzzles WHERE published = TRUE)
  );
