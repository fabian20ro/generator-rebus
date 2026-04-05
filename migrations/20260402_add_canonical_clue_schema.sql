CREATE TABLE IF NOT EXISTS canonical_clue_definitions (
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

ALTER TABLE canonical_clue_definitions
  ADD COLUMN IF NOT EXISTS word_type VARCHAR(8) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS usage_label VARCHAR(16) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS semantic_score SMALLINT,
  ADD COLUMN IF NOT EXISTS rebus_score SMALLINT,
  ADD COLUMN IF NOT EXISTS creativity_score SMALLINT,
  ADD COLUMN IF NOT EXISTS usage_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS superseded_by UUID NULL REFERENCES canonical_clue_definitions(id);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'canonical_clue_definitions_word_normalized_word_type_usage_l_key'
  ) THEN
    ALTER TABLE canonical_clue_definitions
      ADD CONSTRAINT canonical_clue_definitions_word_normalized_word_type_usage_l_key
      UNIQUE (word_normalized, word_type, usage_label, definition_norm);
  END IF;
END $$;

ALTER TABLE crossword_clues
  ADD COLUMN IF NOT EXISTS word_type VARCHAR(8) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS canonical_definition_id UUID NULL REFERENCES canonical_clue_definitions(id);

CREATE TABLE IF NOT EXISTS canonical_clue_aliases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_definition_id UUID NOT NULL REFERENCES canonical_clue_definitions(id) ON DELETE CASCADE,
  source_clue_id UUID NULL REFERENCES crossword_clues(id) ON DELETE SET NULL,
  word_normalized VARCHAR(50) NOT NULL,
  definition TEXT NOT NULL,
  definition_norm TEXT NOT NULL,
  match_type VARCHAR(16) NOT NULL,
  same_meaning_votes SMALLINT,
  winner_votes SMALLINT,
  decision_source VARCHAR(32) NOT NULL,
  decision_note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (canonical_definition_id, word_normalized, definition_norm, source_clue_id)
);

CREATE INDEX IF NOT EXISTS idx_clues_canonical_definition ON crossword_clues(canonical_definition_id);
CREATE INDEX IF NOT EXISTS idx_canonical_clues_word ON canonical_clue_definitions(word_normalized);
CREATE INDEX IF NOT EXISTS idx_canonical_clues_word_meta ON canonical_clue_definitions(word_normalized, word_type, usage_label);
CREATE INDEX IF NOT EXISTS idx_canonical_aliases_word ON canonical_clue_aliases(word_normalized);

ALTER TABLE canonical_clue_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical_clue_aliases ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public read canonical clues of published puzzles" ON canonical_clue_definitions;
CREATE POLICY "Public read canonical clues of published puzzles" ON canonical_clue_definitions
  FOR SELECT USING (
    id IN (
      SELECT canonical_definition_id
      FROM crossword_clues
      WHERE puzzle_id IN (SELECT id FROM crossword_puzzles WHERE published = TRUE)
    )
  );

CREATE OR REPLACE VIEW crossword_clue_effective
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
  COALESCE(ccd.definition, cc.definition) AS definition,
  CASE
    WHEN ccd.id IS NOT NULL THEN 'canonical'
    WHEN cc.definition IS NOT NULL THEN 'legacy'
    ELSE 'missing'
  END::TEXT AS definition_source,
  cc.verify_note,
  cc.verified
FROM crossword_clues cc
LEFT JOIN canonical_clue_definitions ccd ON ccd.id = cc.canonical_definition_id;
