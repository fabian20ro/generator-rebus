BEGIN;

INSERT INTO public.canonical_clue_definitions (
  id, word_normalized, word_original_seed, definition, definition_norm, verified
) VALUES
  ('00000000-0000-0000-0000-000000000001', 'AER', 'aer', 'Gaz din atmosferă', 'gaz din atmosfera', TRUE),
  ('00000000-0000-0000-0000-000000000002', 'AER', 'aer', 'Amestec respirabil', 'amestec respirabil', TRUE);

SET LOCAL ROLE service_role;
CREATE TEMP TABLE publication_results (id UUID);
INSERT INTO publication_results
SELECT public.publish_crossword_puzzle_atomic(
  'smoke-publication',
  jsonb_build_object(
    'title', 'Test', 'grid_size', 1, 'grid_template', '[[true]]',
    'grid_solution', '[["A"]]', 'difficulty', 1, 'published', TRUE
  ),
  jsonb_build_array(jsonb_build_object(
    'direction', 'H', 'start_row', 0, 'start_col', 0, 'length', 1,
    'word_normalized', 'AER', 'word_original', 'aer', 'word_type', '',
    'clue_number', 1,
    'canonical_definition_id', '00000000-0000-0000-0000-000000000001',
    'verified', TRUE
  ))
);
INSERT INTO publication_results SELECT public.publish_crossword_puzzle_atomic(
  'smoke-publication',
  jsonb_build_object(
    'title', 'Test', 'grid_size', 1, 'grid_template', '[[true]]',
    'grid_solution', '[["A"]]', 'difficulty', 1, 'published', TRUE
  ),
  jsonb_build_array(jsonb_build_object(
    'direction', 'H', 'start_row', 0, 'start_col', 0, 'length', 1,
    'word_normalized', 'AER', 'word_original', 'aer', 'word_type', '',
    'clue_number', 1,
    'canonical_definition_id', '00000000-0000-0000-0000-000000000001',
    'verified', TRUE
  ))
);
RESET ROLE;

DO $$
BEGIN
  IF (SELECT COUNT(DISTINCT id) FROM publication_results) <> 1
     OR (SELECT COUNT(*) FROM public.crossword_puzzles) <> 1
     OR (SELECT COUNT(*) FROM public.crossword_clues) <> 1 THEN
    RAISE EXCEPTION 'atomic publication is not idempotent';
  END IF;
  IF (SELECT usage_count FROM public.canonical_clue_definitions
      WHERE id = '00000000-0000-0000-0000-000000000001') <> 1 THEN
    RAISE EXCEPTION 'canonical usage count drifted';
  END IF;
END;
$$;

SET LOCAL ROLE service_role;
CREATE TEMP TABLE merge_results (id UUID);
INSERT INTO merge_results SELECT public.merge_canonical_definitions_atomic(
  ARRAY[
    '00000000-0000-0000-0000-000000000001'::UUID,
    '00000000-0000-0000-0000-000000000002'::UUID
  ],
  'AER', 'aer', 'Gaz respirabil din atmosferă', 'gaz respirabil din atmosfera',
  '', '', TRUE, 9::SMALLINT, 8::SMALLINT, 7::SMALLINT
);
INSERT INTO merge_results SELECT public.merge_canonical_definitions_atomic(
  ARRAY[
    '00000000-0000-0000-0000-000000000001'::UUID,
    '00000000-0000-0000-0000-000000000002'::UUID
  ],
  'AER', 'aer', 'Gaz respirabil din atmosferă', 'gaz respirabil din atmosfera',
  '', '', TRUE, 9::SMALLINT, 8::SMALLINT, 7::SMALLINT
);
RESET ROLE;

DO $$
BEGIN
  IF (SELECT COUNT(DISTINCT id) FROM merge_results) <> 1
     OR (SELECT COUNT(*) FROM public.canonical_clue_definitions) <> 1 THEN
    RAISE EXCEPTION 'atomic canonical merge is not idempotent';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.crossword_clues AS clue
    JOIN public.canonical_clue_definitions AS canonical
      ON canonical.id = clue.canonical_definition_id
    WHERE canonical.usage_count = 1
  ) THEN
    RAISE EXCEPTION 'canonical merge did not repoint clue and usage';
  END IF;
END;
$$;

ROLLBACK;
