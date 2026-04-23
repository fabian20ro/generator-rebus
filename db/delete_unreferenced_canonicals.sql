BEGIN;

DROP TABLE IF EXISTS pg_temp._canonical_cleanup_classified;

CREATE TEMP TABLE _canonical_cleanup_classified ON COMMIT DROP AS
WITH referenced AS (
  SELECT DISTINCT canonical_definition_id AS id
  FROM crossword_clues
  WHERE canonical_definition_id IS NOT NULL
),
ranked AS (
  SELECT
    c.id,
    c.word_normalized,
    coalesce(c.word_type, '') AS word_type,
    coalesce(c.usage_label, '') AS usage_label,
    c.definition,
    c.superseded_by,
    r.id IS NOT NULL AS is_referenced,
    c.superseded_by IS NULL AND btrim(coalesce(c.definition, '')) <> '' AS is_active_valid,
    count(*) FILTER (
      WHERE c.superseded_by IS NULL AND btrim(coalesce(c.definition, '')) <> ''
    ) OVER (
      PARTITION BY c.word_normalized, coalesce(c.word_type, ''), coalesce(c.usage_label, '')
    ) AS active_valid_count,
    dense_rank() OVER (
      PARTITION BY c.word_normalized, coalesce(c.word_type, ''), coalesce(c.usage_label, '')
      ORDER BY
        CASE WHEN c.superseded_by IS NULL AND btrim(coalesce(c.definition, '')) <> '' THEN 0 ELSE 1 END,
        CASE WHEN coalesce(c.verified, false) THEN 0 ELSE 1 END,
        -coalesce(c.semantic_score, -1),
        -coalesce(c.rebus_score, -1),
        -coalesce(c.creativity_score, -1),
        -greatest(coalesce(c.usage_count, 0), 0),
        -extract(epoch FROM coalesce(c.updated_at, 'epoch'::timestamptz))
    ) AS quality_rank
  FROM canonical_clue_definitions c
  LEFT JOIN referenced r ON r.id = c.id
)
SELECT
  id,
  word_normalized,
  word_type,
  usage_label,
  definition,
  superseded_by,
  CASE
    WHEN is_referenced THEN 'referenced'
    WHEN NOT is_active_valid THEN 'unreferenced_redundant_deletable'
    WHEN active_valid_count = 1 THEN 'unreferenced_singleton_fallback'
    WHEN quality_rank = 1 THEN 'unreferenced_best_fallback'
    ELSE 'unreferenced_redundant_deletable'
  END AS cleanup_category
FROM ranked;

SELECT cleanup_category, COUNT(*) AS count
FROM _canonical_cleanup_classified
WHERE cleanup_category <> 'referenced'
GROUP BY cleanup_category
ORDER BY cleanup_category;

SELECT *
FROM _canonical_cleanup_classified
WHERE cleanup_category <> 'referenced'
ORDER BY cleanup_category, word_normalized, id
LIMIT 50;

UPDATE canonical_clue_definitions
SET superseded_by = NULL
WHERE superseded_by IN (
  SELECT id
  FROM _canonical_cleanup_classified
  WHERE cleanup_category = 'unreferenced_redundant_deletable'
);

UPDATE canonical_clue_definitions
SET superseded_by = NULL
WHERE id IN (
  SELECT id
  FROM _canonical_cleanup_classified
  WHERE cleanup_category = 'unreferenced_redundant_deletable'
);

DELETE FROM canonical_clue_definitions
WHERE id IN (
  SELECT id
  FROM _canonical_cleanup_classified
  WHERE cleanup_category = 'unreferenced_redundant_deletable'
);

-- replace rollback with commit after reviewing the preview queries above
ROLLBACK;
-- COMMIT;
