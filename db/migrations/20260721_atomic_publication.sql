ALTER TABLE public.crossword_puzzles
  ADD COLUMN IF NOT EXISTS publication_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS crossword_puzzles_publication_key_key
  ON public.crossword_puzzles(publication_key)
  WHERE publication_key IS NOT NULL;

CREATE OR REPLACE FUNCTION public.publish_crossword_puzzle_atomic(
  p_publication_key TEXT,
  p_puzzle JSONB,
  p_clues JSONB
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  puzzle_id UUID;
  inserted_clues INTEGER;
  expected_clues INTEGER;
BEGIN
  IF COALESCE(BTRIM(p_publication_key), '') = '' THEN
    RAISE EXCEPTION 'publication key is required';
  END IF;
  IF jsonb_typeof(p_clues) <> 'array' THEN
    RAISE EXCEPTION 'clues must be a JSON array';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_publication_key, 0));
  SELECT id INTO puzzle_id
  FROM public.crossword_puzzles
  WHERE publication_key = p_publication_key;
  IF puzzle_id IS NOT NULL THEN
    RETURN puzzle_id;
  END IF;

  expected_clues := jsonb_array_length(p_clues);
  INSERT INTO public.crossword_puzzles (
    title, title_score, description, grid_size, grid_template, grid_solution,
    difficulty, rebus_score_min, rebus_score_avg, definition_score,
    verified_count, total_clues, pass_rate, created_at, updated_at, published,
    publication_key
  ) VALUES (
    NULLIF(p_puzzle->>'title', ''), NULLIF(p_puzzle->>'title_score', '')::SMALLINT,
    NULLIF(p_puzzle->>'description', ''), (p_puzzle->>'grid_size')::SMALLINT,
    p_puzzle->>'grid_template', p_puzzle->>'grid_solution',
    NULLIF(p_puzzle->>'difficulty', '')::SMALLINT,
    NULLIF(p_puzzle->>'rebus_score_min', '')::SMALLINT,
    NULLIF(p_puzzle->>'rebus_score_avg', '')::REAL,
    NULLIF(p_puzzle->>'definition_score', '')::REAL,
    NULLIF(p_puzzle->>'verified_count', '')::SMALLINT,
    NULLIF(p_puzzle->>'total_clues', '')::SMALLINT,
    NULLIF(p_puzzle->>'pass_rate', '')::REAL,
    COALESCE(NULLIF(p_puzzle->>'created_at', '')::TIMESTAMPTZ, NOW()),
    NULLIF(p_puzzle->>'updated_at', '')::TIMESTAMPTZ,
    COALESCE((p_puzzle->>'published')::BOOLEAN, FALSE), p_publication_key
  ) RETURNING id INTO puzzle_id;

  INSERT INTO public.crossword_clues (
    puzzle_id, direction, start_row, start_col, length, word_normalized,
    word_original, word_type, clue_number, canonical_definition_id,
    verify_note, verified
  )
  SELECT
    puzzle_id, clue.direction, clue.start_row, clue.start_col, clue.length,
    clue.word_normalized, clue.word_original, COALESCE(clue.word_type, ''),
    clue.clue_number, clue.canonical_definition_id,
    COALESCE(clue.verify_note, ''), COALESCE(clue.verified, FALSE)
  FROM jsonb_to_recordset(p_clues) AS clue(
    direction CHAR(1), start_row SMALLINT, start_col SMALLINT, length SMALLINT,
    word_normalized VARCHAR(50), word_original VARCHAR(50), word_type VARCHAR(8),
    clue_number SMALLINT, canonical_definition_id UUID, verify_note TEXT,
    verified BOOLEAN
  );

  GET DIAGNOSTICS inserted_clues = ROW_COUNT;
  IF inserted_clues <> expected_clues THEN
    RAISE EXCEPTION 'slot-clue mismatch: expected %, inserted %', expected_clues, inserted_clues;
  END IF;

  UPDATE public.canonical_clue_definitions AS canonical
  SET usage_count = usage.count,
      last_used_at = NOW(),
      updated_at = NOW()
  FROM (
    SELECT clue.canonical_definition_id, COUNT(*)::INTEGER AS count
    FROM public.crossword_clues AS clue
    WHERE clue.canonical_definition_id IN (
      SELECT DISTINCT record.canonical_definition_id
      FROM jsonb_to_recordset(p_clues) AS record(canonical_definition_id UUID)
    )
    GROUP BY clue.canonical_definition_id
  ) AS usage
  WHERE canonical.id = usage.canonical_definition_id;
  RETURN puzzle_id;
END;
$$;

REVOKE ALL ON FUNCTION public.publish_crossword_puzzle_atomic(TEXT, JSONB, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.publish_crossword_puzzle_atomic(TEXT, JSONB, JSONB) TO service_role;

CREATE OR REPLACE FUNCTION public.merge_canonical_definitions_atomic(
  p_source_ids UUID[],
  p_word_normalized TEXT,
  p_word_original_seed TEXT,
  p_definition TEXT,
  p_definition_norm TEXT,
  p_word_type TEXT,
  p_usage_label TEXT,
  p_verified BOOLEAN,
  p_semantic_score SMALLINT,
  p_rebus_score SMALLINT,
  p_creativity_score SMALLINT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  survivor_id UUID;
BEGIN
  IF COALESCE(array_length(p_source_ids, 1), 0) < 2 THEN
    RAISE EXCEPTION 'at least two canonical source IDs are required';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(
    p_word_normalized || E'\x1f' || p_word_type || E'\x1f' || p_usage_label || E'\x1f' || p_definition_norm,
    0
  ));

  INSERT INTO public.canonical_clue_definitions (
    word_normalized, word_original_seed, definition, definition_norm,
    word_type, usage_label, verified, semantic_score, rebus_score,
    creativity_score, usage_count, updated_at, last_used_at
  ) VALUES (
    p_word_normalized, p_word_original_seed, p_definition, p_definition_norm,
    p_word_type, p_usage_label, p_verified, p_semantic_score, p_rebus_score,
    p_creativity_score, 0, NOW(), NOW()
  )
  ON CONFLICT (word_normalized, word_type, usage_label, definition_norm)
  DO UPDATE SET updated_at = NOW()
  RETURNING id INTO survivor_id;

  UPDATE public.crossword_clues
  SET canonical_definition_id = survivor_id
  WHERE canonical_definition_id = ANY(p_source_ids)
    AND canonical_definition_id <> survivor_id;

  UPDATE public.canonical_clue_definitions
  SET superseded_by = survivor_id, updated_at = NOW()
  WHERE id = ANY(p_source_ids) AND id <> survivor_id;

  UPDATE public.canonical_clue_definitions
  SET usage_count = (
    SELECT COUNT(*) FROM public.crossword_clues
    WHERE canonical_definition_id = survivor_id
  ), last_used_at = NOW(), updated_at = NOW()
  WHERE id = survivor_id;

  DELETE FROM public.canonical_clue_definitions AS source
  WHERE source.id = ANY(p_source_ids)
    AND source.id <> survivor_id
    AND NOT EXISTS (
      SELECT 1 FROM public.crossword_clues
      WHERE canonical_definition_id = source.id
    );
  RETURN survivor_id;
END;
$$;

REVOKE ALL ON FUNCTION public.merge_canonical_definitions_atomic(
  UUID[], TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, SMALLINT, SMALLINT, SMALLINT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.merge_canonical_definitions_atomic(
  UUID[], TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, SMALLINT, SMALLINT, SMALLINT
) TO service_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.crossword_puzzles TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.crossword_clues TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.canonical_clue_definitions TO service_role;
