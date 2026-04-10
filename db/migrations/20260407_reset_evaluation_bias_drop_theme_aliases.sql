BEGIN;

UPDATE crossword_puzzles
SET
  title_score = NULL,
  rebus_score_min = NULL,
  rebus_score_avg = NULL,
  definition_score = NULL,
  verified_count = NULL,
  pass_rate = NULL,
  updated_at = NOW();

UPDATE crossword_clues
SET
  verified = FALSE,
  verify_note = '';

UPDATE canonical_clue_definitions
SET
  verified = FALSE,
  semantic_score = NULL,
  rebus_score = NULL,
  creativity_score = NULL,
  usage_count = 0,
  updated_at = NOW();

DROP TABLE IF EXISTS canonical_clue_aliases;

ALTER TABLE crossword_puzzles
  DROP COLUMN IF EXISTS theme;

COMMIT;
