BEGIN;

WITH referenced AS (
  SELECT DISTINCT canonical_definition_id AS id
  FROM crossword_clues
),
doomed AS (
  SELECT c.id, c.word_normalized, c.definition, c.superseded_by
  FROM canonical_clue_definitions c
  LEFT JOIN referenced r ON r.id = c.id
  WHERE r.id IS NULL
)
SELECT COUNT(*) AS doomed_count
FROM doomed;

WITH referenced AS (
  SELECT DISTINCT canonical_definition_id AS id
  FROM crossword_clues
),
doomed AS (
  SELECT c.id, c.word_normalized, c.definition, c.superseded_by
  FROM canonical_clue_definitions c
  LEFT JOIN referenced r ON r.id = c.id
  WHERE r.id IS NULL
)
SELECT *
FROM doomed
ORDER BY word_normalized, id
LIMIT 50;

WITH referenced AS (
  SELECT DISTINCT canonical_definition_id AS id
  FROM crossword_clues
),
doomed AS (
  SELECT c.id
  FROM canonical_clue_definitions c
  LEFT JOIN referenced r ON r.id = c.id
  WHERE r.id IS NULL
)
UPDATE canonical_clue_definitions
SET superseded_by = NULL
WHERE superseded_by IN (SELECT id FROM doomed);

WITH referenced AS (
  SELECT DISTINCT canonical_definition_id AS id
  FROM crossword_clues
),
doomed AS (
  SELECT c.id
  FROM canonical_clue_definitions c
  LEFT JOIN referenced r ON r.id = c.id
  WHERE r.id IS NULL
)
UPDATE canonical_clue_definitions
SET superseded_by = NULL
WHERE id IN (SELECT id FROM doomed);

WITH referenced AS (
  SELECT DISTINCT canonical_definition_id AS id
  FROM crossword_clues
),
doomed AS (
  SELECT c.id
  FROM canonical_clue_definitions c
  LEFT JOIN referenced r ON r.id = c.id
  WHERE r.id IS NULL
)
DELETE FROM canonical_clue_definitions
WHERE id IN (SELECT id FROM doomed);

-- optional: verify inside same transaction what the post-delete state would be
SELECT COUNT(*) AS remaining_unreferenced
FROM canonical_clue_definitions c
LEFT JOIN (
  SELECT DISTINCT canonical_definition_id AS id
  FROM crossword_clues
) r ON r.id = c.id
WHERE r.id IS NULL;

-- replace rollback with commit. execute at least one so the transaction doesn't remain active
rollback;
-- commit;
