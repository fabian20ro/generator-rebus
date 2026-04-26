create or replace function public.run_all_grid_size_counts()
returns table(grid_size integer, puzzle_count bigint)
language sql
stable
as $$
  select cp.grid_size, count(*)::bigint as puzzle_count
  from public.crossword_puzzles cp
  where cp.grid_size is not null
  group by cp.grid_size
  order by cp.grid_size;
$$;

create or replace function public.run_all_redefine_candidates(limit_count integer default 200)
returns table(
  id uuid,
  title text,
  grid_size integer,
  created_at timestamptz,
  repaired_at timestamptz,
  description text,
  rebus_score_min integer,
  rebus_score_avg numeric,
  definition_score numeric,
  verified_count integer,
  total_clues integer,
  pass_rate numeric
)
language sql
stable
as $$
  select
    cp.id,
    cp.title,
    cp.grid_size,
    cp.created_at,
    cp.repaired_at,
    cp.description,
    cp.rebus_score_min,
    cp.rebus_score_avg,
    cp.definition_score,
    cp.verified_count,
    cp.total_clues,
    cp.pass_rate
  from public.crossword_puzzles cp
  order by
    case when cp.repaired_at is null then 0 else 1 end,
    case
      when cp.description is null or btrim(cp.description) = '' then 0
      when cp.rebus_score_min is null then 0
      when cp.rebus_score_avg is null then 0
      when cp.definition_score is null then 0
      when cp.verified_count is null then 0
      when cp.total_clues is null then 0
      when cp.pass_rate is null then 0
      else 1
    end,
    coalesce(cp.repaired_at, cp.created_at),
    cp.created_at nulls last,
    cp.id
  limit greatest(1, limit_count);
$$;

create or replace function public.run_all_retitle_candidates(limit_count integer default 200)
returns table(
  id uuid,
  title text,
  title_score integer,
  created_at timestamptz
)
language sql
stable
as $$
  select cp.id, cp.title, cp.title_score, cp.created_at
  from public.crossword_puzzles cp
  order by
    case when cp.title_score is null then 0 else 1 end,
    cp.created_at nulls last,
    cp.id
  limit greatest(1, limit_count);
$$;

create or replace function public.run_all_simplify_candidate_pairs(
  limit_count integer default 100,
  excluded_words text[] default '{}'
)
returns table(
  key text,
  word text,
  word_type text,
  usage_label text,
  left_id uuid,
  right_id uuid,
  left_definition text,
  right_definition text,
  left_definition_norm text,
  right_definition_norm text,
  weight numeric
)
language sql
stable
as $$
  with active as (
    select *
    from public.canonical_clue_definitions c
    where c.superseded_by is null
      and not (c.word_normalized = any(coalesce(excluded_words, '{}')))
  ),
  bucket_sizes as (
    select word_normalized, word_type, usage_label, count(*)::numeric as bucket_size
    from active
    group by word_normalized, word_type, usage_label
    having count(*) > 1
  )
  select
    least(l.id, r.id)::text || '::' || greatest(l.id, r.id)::text as key,
    l.word_normalized as word,
    l.word_type,
    l.usage_label,
    l.id as left_id,
    r.id as right_id,
    l.definition as left_definition,
    r.definition as right_definition,
    l.definition_norm as left_definition_norm,
    r.definition_norm as right_definition_norm,
    greatest(0.1, bs.bucket_size) as weight
  from active l
  join active r
    on l.word_normalized = r.word_normalized
   and l.word_type = r.word_type
   and l.usage_label = r.usage_label
   and l.id < r.id
   and l.definition_norm <> r.definition_norm
  join bucket_sizes bs
    on bs.word_normalized = l.word_normalized
   and bs.word_type = l.word_type
   and bs.usage_label = l.usage_label
  order by weight desc, word, left_id, right_id
  limit greatest(1, limit_count);
$$;
