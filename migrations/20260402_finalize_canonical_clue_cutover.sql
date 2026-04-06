-- Run only after the canonical cutover migration has populated
-- `crossword_clues.canonical_definition_id` for every live row
-- and `python -m generator.clue_canon audit` reports ok=true.
-- and after every live clue row has a non-null canonical_definition_id.
--
-- Preflight dependency checks before production cutover:
--
-- Dependents of `crossword_clue_effective`:
-- SELECT
--   dependent_ns.nspname AS dependent_schema,
--   dependent_obj.relname AS dependent_object,
--   dependent_obj.relkind AS dependent_kind
-- FROM pg_depend dep
-- JOIN pg_rewrite rw ON dep.objid = rw.oid
-- JOIN pg_class dependent_obj ON rw.ev_class = dependent_obj.oid
-- JOIN pg_namespace dependent_ns ON dependent_obj.relnamespace = dependent_ns.oid
-- WHERE dep.refobjid = 'public.crossword_clue_effective'::regclass
-- ORDER BY 1, 2;
--
-- Dependents of `crossword_clues.definition`:
-- SELECT
--   n.nspname AS table_schema,
--   c.relname AS table_name,
--   a.attname AS column_name,
--   dep.deptype
-- FROM pg_depend dep
-- JOIN pg_attribute a
--   ON dep.refobjid = a.attrelid
--  AND dep.refobjsubid = a.attnum
-- JOIN pg_class c ON a.attrelid = c.oid
-- JOIN pg_namespace n ON c.relnamespace = n.oid
-- WHERE a.attrelid = 'public.crossword_clues'::regclass
--   AND a.attname = 'definition';
--
-- Manual fallback if a previous run failed mid-cutover:
--   1. SELECT count(*) FROM crossword_clues WHERE canonical_definition_id IS NULL;
--   2. CREATE OR REPLACE VIEW crossword_clue_effective ... (canonical-only form below)
--   3. ALTER TABLE crossword_clues DROP COLUMN definition;
--
-- Do NOT use `DROP COLUMN ... CASCADE` blindly. The compatibility view is the
-- expected blocker, and preserving it via `CREATE OR REPLACE VIEW` is safer
-- than dropping unknown downstream dependents.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM crossword_clues
    WHERE canonical_definition_id IS NULL
  ) THEN
    RAISE EXCEPTION 'Cutover blocked: crossword_clues still has rows without canonical_definition_id';
  END IF;
END $$;

ALTER TABLE crossword_clues
  ALTER COLUMN canonical_definition_id SET NOT NULL;

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
  ccd.definition,
  'canonical'::TEXT AS definition_source,
  cc.verify_note,
  cc.verified
FROM crossword_clues cc
JOIN canonical_clue_definitions ccd ON ccd.id = cc.canonical_definition_id;

ALTER TABLE crossword_clues
  DROP COLUMN IF EXISTS definition;
