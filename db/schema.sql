-- Romanian Rebus Generator - Database Schema
-- Run this in Supabase SQL Editor for a fresh install.

CREATE TABLE crossword_puzzles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(100),
  title_score SMALLINT,
  description TEXT,
  grid_size SMALLINT NOT NULL DEFAULT 10,
  grid_template TEXT NOT NULL,
  grid_solution TEXT NOT NULL,
  difficulty SMALLINT DEFAULT 3,
  rebus_score_min SMALLINT,
  rebus_score_avg REAL,
  definition_score REAL,
  verified_count SMALLINT,
  total_clues SMALLINT,
  pass_rate REAL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ,
  repaired_at TIMESTAMPTZ,
  published BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE canonical_clue_definitions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  word_normalized VARCHAR(50) NOT NULL,
  word_original_seed VARCHAR(50) NOT NULL,
  definition TEXT NOT NULL,
  definition_norm TEXT NOT NULL,
  word_type VARCHAR(8) NOT NULL DEFAULT '',
  usage_label VARCHAR(16) NOT NULL DEFAULT '',
  verified BOOLEAN NOT NULL DEFAULT FALSE,
  semantic_score SMALLINT,
  rebus_score SMALLINT,
  creativity_score SMALLINT,
  usage_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ,
  last_used_at TIMESTAMPTZ,
  superseded_by UUID NULL REFERENCES canonical_clue_definitions(id),
  UNIQUE (word_normalized, word_type, usage_label, definition_norm)
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
  word_type VARCHAR(8) NOT NULL DEFAULT '',
  clue_number SMALLINT NOT NULL,
  canonical_definition_id UUID NOT NULL REFERENCES canonical_clue_definitions(id),
  verify_note TEXT NOT NULL DEFAULT '',
  verified BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (puzzle_id, direction, start_row, start_col)
);

CREATE INDEX idx_clues_puzzle ON crossword_clues(puzzle_id);
CREATE INDEX idx_clues_canonical_definition ON crossword_clues(canonical_definition_id);
CREATE INDEX idx_puzzles_published ON crossword_puzzles(published) WHERE published = TRUE;
CREATE INDEX idx_canonical_clues_word ON canonical_clue_definitions(word_normalized);
CREATE INDEX idx_canonical_clues_word_meta ON canonical_clue_definitions(word_normalized, word_type, usage_label);

CREATE VIEW crossword_clue_effective
WITH (security_invoker = true) AS
SELECT
  cc.id,
  cc.puzzle_id,
  cc.direction,
  cc.start_row,
  cc.start_col,
  cc.length,
  cc.word_normalized,
  cc.word_original,
  cc.word_type,
  cc.clue_number,
  cc.canonical_definition_id,
  ccd.definition,
  'canonical'::TEXT AS definition_source,
  cc.verify_note,
  cc.verified
FROM crossword_clues cc
JOIN canonical_clue_definitions ccd ON ccd.id = cc.canonical_definition_id;

-- Row-Level Security
ALTER TABLE crossword_puzzles ENABLE ROW LEVEL SECURITY;
ALTER TABLE crossword_clues ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical_clue_definitions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read published" ON crossword_puzzles
  FOR SELECT USING (published = TRUE);

CREATE POLICY "Public read clues of published puzzles" ON crossword_clues
  FOR SELECT USING (
    puzzle_id IN (SELECT id FROM crossword_puzzles WHERE published = TRUE)
  );

CREATE POLICY "Public read canonical clues of published puzzles" ON canonical_clue_definitions
  FOR SELECT USING (
    id IN (
      SELECT canonical_definition_id
      FROM crossword_clues
      WHERE puzzle_id IN (SELECT id FROM crossword_puzzles WHERE published = TRUE)
    )
  );

-- Dexonline definition cache (shared with propozitii-nostime)
CREATE TABLE dex_definitions (
  word TEXT PRIMARY KEY,
  original TEXT NOT NULL,
  html TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  fetched_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dex_definitions_status ON dex_definitions(status);

ALTER TABLE dex_definitions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read definitions" ON dex_definitions
  FOR SELECT USING (true);
