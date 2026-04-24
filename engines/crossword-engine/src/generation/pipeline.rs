use std::cmp::Ordering;
use std::collections::HashSet;
use std::fs;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::time::Instant;

use rand::SeedableRng;
use rand::rngs::StdRng;
use rayon::prelude::*;

use super::candidate::CandidateResult;
use super::output::{EngineError, EngineOutput, OutputWord, SearchStats};
use super::settings::{SizeSettings, plan_search_effort, settings_for_size};
use crate::dictionary_profile::{
    RuntimeSizeDictionaryProfile, dictionary_profile_path, load_runtime_dictionary_profile,
};
use crate::quality::{QualityReport, score_words};
use crate::slots::{Slot, extract_slots};
use crate::solver::{WordIndex, solve_grid};
use crate::template::{
    count_edge_singletons, generate_incremental_template, generate_procedural_template,
    template_rejection_bucket, validate_template,
};
use crate::words::{RawWord, filter_word_records};

fn count_two_letter_slots(grid: &[Vec<bool>]) -> usize {
    let rows = grid.len();
    let cols = grid.first().map_or(0, |row| row.len());
    let mut count = 0usize;
    for r in 0..rows {
        let mut run = 0usize;
        for c in 0..=cols {
            if c < cols && grid[r][c] {
                run += 1;
            } else {
                if run == 2 {
                    count += 1;
                }
                run = 0;
            }
        }
    }
    for c in 0..cols {
        let mut run = 0usize;
        for r in 0..=rows {
            if r < rows && grid[r][c] {
                run += 1;
            } else {
                if run == 2 {
                    count += 1;
                }
                run = 0;
            }
        }
    }
    count
}

fn count_black_cells(grid: &[Vec<bool>]) -> usize {
    grid.iter()
        .map(|row| row.iter().filter(|cell| !**cell).count())
        .sum()
}

fn slot_capacity_ok(slots: &[Slot], index: &WordIndex, settings: SizeSettings) -> bool {
    for slot in slots {
        let required = if slot.length >= 10 {
            1
        } else {
            settings.min_candidates_per_slot
        };
        if index.count_matching(&vec![None; slot.length], None) < required {
            return false;
        }
    }
    true
}

fn template_ok(
    template: &[Vec<bool>],
    slots: &[Slot],
    settings: SizeSettings,
    size: usize,
) -> Result<usize, &'static str> {
    if let Err(message) = validate_template(template) {
        return Err(template_rejection_bucket(&message));
    }
    if count_two_letter_slots(template) > settings.max_two_letter_slots {
        return Err("other");
    }
    if let Some(limit) = settings.max_full_width_slots {
        let full_width = slots.iter().filter(|slot| slot.length == size).count();
        if full_width > limit {
            return Err("other");
        }
    }
    Ok(count_edge_singletons(template))
}

const SALT_PROCEDURAL_TEMPLATE: u64 = 0x000A_11CE;
const SALT_EVALUATE_TEMPLATE: u64 = 0x0A57_DA12;
const SALT_INCREMENTAL_BASE: u64 = 0x1CE0_0001;
const SALT_SOLVE_GRID_PROBE: u64 = 0x0BEE_F123;

fn template_seed(base_seed: u64, black_step: usize, attempt_idx: usize, salt: u64) -> u64 {
    base_seed
        ^ ((black_step as u64 + 1) << 48)
        ^ ((attempt_idx as u64 + 1).wrapping_mul(0x9E37_79B9_7F4A_7C15))
        ^ salt
}

fn render_words(
    slots: &[Slot],
    assignment: &[usize],
    index: &WordIndex,
) -> Result<Vec<OutputWord>, EngineError> {
    let mut rendered = Vec::with_capacity(slots.len());
    for slot in slots {
        let Some(word_idx) = assignment.get(slot.id).copied() else {
            return Err(EngineError::InternalInvariant("missing assignment"));
        };
        let Some(bucket) = index.bucket(slot.length) else {
            return Err(EngineError::InternalInvariant("missing bucket"));
        };
        let Some(word) = bucket.words.get(word_idx) else {
            return Err(EngineError::InternalInvariant("word index out of bounds"));
        };
        rendered.push(OutputWord {
            slot_id: slot.id,
            normalized: word.normalized.clone(),
            original: word.original.clone(),
            source: word.source.clone(),
        });
    }
    Ok(rendered)
}

fn candidate_quality_order(candidate: &CandidateResult, current: &CandidateResult) -> Ordering {
    let candidate_key = (
        ReverseOrd(candidate.stats.edge_singletons as f64),
        candidate.report.middle_window_average_length,
        candidate.report.average_length,
        ReverseOrd(candidate.report.two_letter_words as f64),
        ReverseOrd(candidate.report.three_letter_words as f64),
        candidate.report.average_definability,
        ReverseOrd(candidate.report.uncommon_letter_words as f64),
    );
    let current_key = (
        ReverseOrd(current.stats.edge_singletons as f64),
        current.report.middle_window_average_length,
        current.report.average_length,
        ReverseOrd(current.report.two_letter_words as f64),
        ReverseOrd(current.report.three_letter_words as f64),
        current.report.average_definability,
        ReverseOrd(current.report.uncommon_letter_words as f64),
    );
    compare_rank(candidate_key, current_key)
}

#[derive(Clone, Copy)]
struct ReverseOrd(f64);

fn compare_f64(left: f64, right: f64) -> Ordering {
    left.partial_cmp(&right).unwrap_or(Ordering::Equal)
}

fn compare_rank(
    left: (ReverseOrd, f64, f64, ReverseOrd, ReverseOrd, f64, ReverseOrd),
    right: (ReverseOrd, f64, f64, ReverseOrd, ReverseOrd, f64, ReverseOrd),
) -> Ordering {
    compare_f64(right.0.0, left.0.0)
        .then_with(|| compare_f64(left.1, right.1))
        .then_with(|| compare_f64(left.2, right.2))
        .then_with(|| compare_f64(right.3.0, left.3.0))
        .then_with(|| compare_f64(right.4.0, left.4.0))
        .then_with(|| compare_f64(left.5, right.5))
        .then_with(|| compare_f64(right.6.0, left.6.0))
}

fn record_rejection(stats: &mut SearchStats, bucket: &str) {
    match bucket {
        "spacing" => stats.rejected_spacing += 1,
        "singleton_interior" => stats.rejected_singleton_interior += 1,
        "disconnected" => stats.rejected_disconnected += 1,
        "uncovered_white" => stats.rejected_uncovered_white += 1,
        _ => {}
    }
}

fn tie_hash(template: &[Vec<bool>], seed: u64) -> u64 {
    let mut hash = seed ^ 0x9E37_79B9_7F4A_7C15;
    for row in template {
        for cell in row {
            hash ^= if *cell {
                0xA24B_AED4_0FBF_6C89
            } else {
                0x9FB2_1C65_1E98_DF25
            };
            hash = hash.rotate_left(7).wrapping_mul(0x9E37_79B9_7F4A_7C15);
        }
    }
    hash
}

fn template_fingerprint(template: &[Vec<bool>]) -> u64 {
    tie_hash(template, 0xC0FF_EE12_3456_7890)
}

fn prefer_candidate(candidate: &CandidateResult, current: &CandidateResult, seed: u64) -> bool {
    match candidate_quality_order(candidate, current) {
        Ordering::Greater => true,
        Ordering::Less => false,
        Ordering::Equal => tie_hash(&candidate.template, seed) < tie_hash(&current.template, seed),
    }
}

fn render_candidate_grid(candidate: &CandidateResult) -> String {
    candidate
        .template
        .iter()
        .enumerate()
        .map(|(row_index, row)| {
            row.iter()
                .enumerate()
                .map(|(col_index, cell)| {
                    if !*cell {
                        '+'
                    } else {
                        candidate.filled_grid[row_index][col_index].unwrap_or('?')
                    }
                })
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Default)]
struct AttemptOutcome {
    candidate: Option<CandidateResult>,
    stats: SearchStats,
}

fn evaluate_template(
    template: Vec<Vec<bool>>,
    size: usize,
    settings: SizeSettings,
    index: &WordIndex,
    scarcity: Option<&RuntimeSizeDictionaryProfile>,
    cancel: &AtomicBool,
    deadline: Instant,
    rng: &mut StdRng,
) -> AttemptOutcome {
    let mut stats = SearchStats::default();
    let slots = extract_slots(&template);
    let edge_singletons = match template_ok(&template, &slots, settings, size) {
        Ok(count) => count,
        Err(bucket) => {
            record_rejection(&mut stats, bucket);
            return AttemptOutcome {
                candidate: None,
                stats,
            };
        }
    };
    if !slot_capacity_ok(&slots, index, settings) {
        stats.rejected_slot_capacity += 1;
        return AttemptOutcome {
            candidate: None,
            stats,
        };
    }

    let (solved, solve_stats) = solve_grid(
        &template,
        &slots,
        index,
        scarcity,
        settings.max_nodes,
        false,
        cancel,
        Some(deadline),
        rng,
    );
    stats.solver_nodes = solve_stats.nodes;
    if solved.is_none() {
        if solve_stats.timed_out {
            stats.rejected_solver_timeout += 1;
        } else {
            stats.rejected_solver_unsat += 1;
        }
        return AttemptOutcome {
            candidate: None,
            stats,
        };
    }

    let (assignment, filled_grid) = solved.expect("checked is_some");
    let mut solved_words = Vec::with_capacity(slots.len());
    let mut flat_assignment = Vec::with_capacity(slots.len());
    for slot in &slots {
        let Some(word_idx) = assignment.get(slot.id).and_then(|value| *value) else {
            return AttemptOutcome {
                candidate: None,
                stats,
            };
        };
        let Some(bucket) = index.bucket(slot.length) else {
            return AttemptOutcome {
                candidate: None,
                stats,
            };
        };
        let Some(word) = bucket.words.get(word_idx) else {
            return AttemptOutcome {
                candidate: None,
                stats,
            };
        };
        solved_words.push(word);
        flat_assignment.push(word_idx);
    }
    let report = score_words(&solved_words, size);
    stats.solved_candidates = 1;
    stats.edge_singletons = edge_singletons;
    stats.inward_solutions_found = 1;
    AttemptOutcome {
        candidate: Some(CandidateResult {
            report,
            template,
            filled_grid,
            slots,
            assignment: flat_assignment,
            stats: stats.clone(),
        }),
        stats,
    }
}

fn run_single_candidate(
    size: usize,
    settings: SizeSettings,
    index: &WordIndex,
    scarcity: Option<&RuntimeSizeDictionaryProfile>,
    base_seed: u64,
    black_step: usize,
    attempt_idx: usize,
    incremental_template: Option<&[Vec<bool>]>,
    cancel: &AtomicBool,
    deadline: Instant,
) -> AttemptOutcome {
    if cancel.load(AtomicOrdering::Relaxed) || Instant::now() >= deadline {
        return AttemptOutcome::default();
    }

    let mut rng = StdRng::seed_from_u64(template_seed(
        base_seed,
        black_step,
        attempt_idx,
        SALT_PROCEDURAL_TEMPLATE,
    ));
    let template = if let Some(template) = incremental_template {
        template.to_vec()
    } else {
        match generate_procedural_template(
            size,
            settings.target_blacks,
            settings.template_attempts,
            &mut rng,
        ) {
            Some(template) => template,
            None => return AttemptOutcome::default(),
        }
    };
    evaluate_template(
        template, size, settings, index, scarcity, cancel, deadline, &mut rng,
    )
}

fn combine_stats(left: &mut SearchStats, right: &SearchStats) {
    left.variants_tried += right.variants_tried;
    left.solved_candidates += right.solved_candidates;
    left.solver_nodes += right.solver_nodes;
    left.inward_solutions_found += right.inward_solutions_found;
    left.outward_removal_attempts += right.outward_removal_attempts;
    left.outward_removal_successes += right.outward_removal_successes;
    left.rejected_spacing += right.rejected_spacing;
    left.rejected_singleton_interior += right.rejected_singleton_interior;
    left.rejected_disconnected += right.rejected_disconnected;
    left.rejected_uncovered_white += right.rejected_uncovered_white;
    left.rejected_slot_capacity += right.rejected_slot_capacity;
    left.rejected_solver_unsat += right.rejected_solver_unsat;
    left.rejected_solver_timeout += right.rejected_solver_timeout;
}

pub fn difficulty_from_quality(size: usize, report: &QualityReport) -> i32 {
    let mut difficulty = match size {
        0..=7 => 2,
        8..=9 => 3,
        10..=11 => 4,
        _ => 5,
    };
    if report.two_letter_words >= (size / 2).max(4) {
        difficulty -= 1;
    }
    if report.average_length >= 6.0 && report.two_letter_words <= 2 {
        difficulty += 1;
    }
    difficulty.clamp(1, 5)
}

fn better_with_fewer_blacks(
    candidate: &CandidateResult,
    current: &CandidateResult,
    seed: u64,
) -> bool {
    let candidate_blacks = count_black_cells(&candidate.template);
    let current_blacks = count_black_cells(&current.template);
    if candidate_blacks != current_blacks {
        return candidate_blacks < current_blacks;
    }
    prefer_candidate(candidate, current, seed)
}

fn run_outward_phase(
    size: usize,
    settings: SizeSettings,
    index: &WordIndex,
    scarcity: Option<&RuntimeSizeDictionaryProfile>,
    seed: u64,
    black_step: usize,
    current: CandidateResult,
    deadline: Instant,
    overall_stats: &mut SearchStats,
) -> CandidateResult {
    let outward_started_at = Instant::now();
    if count_black_cells(&current.template) == 0 {
        overall_stats.outward_skipped_zero_black = true;
        overall_stats.outward_elapsed_ms = outward_started_at.elapsed().as_millis();
        return current;
    }
    let mut best_overall = current.clone();
    let mut frontier = vec![current];
    let mut outward_round = 0usize;

    loop {
        if Instant::now() >= deadline {
            overall_stats.budget_exhausted = true;
            break;
        }

        let removals: Vec<(usize, usize, usize, Vec<Vec<bool>>)> = frontier
            .iter()
            .enumerate()
            .flat_map(|(beam_idx, candidate)| {
                candidate
                    .template
                    .iter()
                    .enumerate()
                    .flat_map(move |(r, row)| {
                        row.iter().enumerate().filter_map(move |(c, cell)| {
                            if !*cell {
                                Some((beam_idx, r, c, candidate.template.clone()))
                            } else {
                                None
                            }
                        })
                    })
                    .collect::<Vec<_>>()
            })
            .collect();

        let outcomes: Vec<AttemptOutcome> = removals
            .into_par_iter()
            .enumerate()
            .map(|(idx, (_beam_idx, r, c, base_template))| {
                let mut stats = SearchStats {
                    outward_removal_attempts: 1,
                    ..SearchStats::default()
                };
                if Instant::now() >= deadline {
                    return AttemptOutcome {
                        candidate: None,
                        stats,
                    };
                }
                let mut template = base_template;
                template[r][c] = true;
                let mut rng = StdRng::seed_from_u64(template_seed(
                    seed,
                    black_step,
                    outward_round * 100_000 + idx,
                    SALT_EVALUATE_TEMPLATE,
                ));
                let mut outcome = evaluate_template(
                    template,
                    size,
                    settings,
                    index,
                    scarcity,
                    &AtomicBool::new(false),
                    deadline,
                    &mut rng,
                );
                combine_stats(&mut stats, &outcome.stats);
                if let Some(candidate) = outcome.candidate.as_mut() {
                    candidate.stats.outward_removal_attempts += 1;
                    candidate.stats.outward_removal_successes += 1;
                    stats.outward_removal_successes += 1;
                }
                AttemptOutcome {
                    candidate: outcome.candidate,
                    stats,
                }
            })
            .collect();

        let mut next_frontier: Vec<CandidateResult> = Vec::new();
        let mut seen_templates: HashSet<u64> = HashSet::new();
        for outcome in outcomes {
            combine_stats(overall_stats, &outcome.stats);
            if let Some(candidate) = outcome.candidate {
                let fingerprint = template_fingerprint(&candidate.template);
                if !seen_templates.insert(fingerprint) {
                    continue;
                }
                if better_with_fewer_blacks(&candidate, &best_overall, seed) {
                    best_overall = candidate.clone();
                }
                next_frontier.push(candidate);
            }
        }

        if next_frontier.is_empty() {
            break;
        }
        next_frontier.sort_by(|left, right| {
            if better_with_fewer_blacks(left, right, seed) {
                Ordering::Less
            } else if better_with_fewer_blacks(right, left, seed) {
                Ordering::Greater
            } else {
                Ordering::Equal
            }
        });
        next_frontier.truncate(settings.outward_beam_width.max(1));
        frontier = next_frontier;
        outward_round += 1;
    }
    overall_stats.outward_elapsed_ms = outward_started_at.elapsed().as_millis();
    overall_stats.outward_rounds = outward_round;
    best_overall
}

pub fn run_engine(
    size: usize,
    words_path: &str,
    seed: u64,
    preparation_attempts: usize,
    step_time_budget_ms_override: Option<u64>,
) -> Result<EngineOutput, EngineError> {
    let started_at = Instant::now();
    let inward_started_at = Instant::now();
    let base_settings = settings_for_size(size).ok_or(EngineError::UnsupportedSize(size))?;
    let raw_words: Vec<RawWord> =
        serde_json::from_str(&fs::read_to_string(words_path).map_err(EngineError::ReadWords)?)
            .map_err(EngineError::ParseWords)?;
    let (filtered_words, dictionary_stats) = filter_word_records(&raw_words, size);
    if filtered_words.is_empty() {
        return Err(EngineError::NoUsableWords(size));
    }
    let dictionary_profile = match load_runtime_dictionary_profile(words_path) {
        Ok(profile) => Some(profile),
        Err(err) => {
            eprintln!(
                "dictionary profile unavailable path={} error={}",
                dictionary_profile_path(words_path).display(),
                err
            );
            None
        }
    };
    let scarcity = dictionary_profile
        .as_ref()
        .and_then(|profile| profile.size(size));
    let (settings, effort_summary) = plan_search_effort(base_settings, scarcity);
    let index = WordIndex::new(&filtered_words);
    eprintln!(
        "dictionary profile size={} loaded={} density_gap={:.3} medium_density={:.3} long_density={:.3} base_target_blacks={} effective_max_nodes={} effective_template_attempts={} effective_min_candidates_per_slot={} positional_rarity_enabled={}",
        size,
        effort_summary.dictionary_profile_loaded,
        effort_summary.density_gap,
        effort_summary.medium_density,
        effort_summary.long_density,
        base_settings.target_blacks,
        settings.max_nodes,
        settings.template_attempts,
        settings.min_candidates_per_slot,
        scarcity.is_some(),
    );
    let mut overall_stats = SearchStats {
        base_target_blacks: base_settings.target_blacks,
        dictionary_total_rows: dictionary_stats.total_rows,
        dictionary_unique_words: dictionary_stats.unique_words,
        dictionary_duplicate_rows: dictionary_stats.duplicate_rows,
        dictionary_skipped_rows: dictionary_stats.skipped_rows,
        dictionary_profile_loaded: effort_summary.dictionary_profile_loaded,
        dictionary_profile_medium_density: effort_summary.medium_density,
        dictionary_profile_long_density: effort_summary.long_density,
        dictionary_profile_density_gap: effort_summary.density_gap,
        effective_max_nodes: settings.max_nodes,
        effective_template_attempts: settings.template_attempts,
        effective_min_candidates_per_slot: settings.min_candidates_per_slot,
        positional_rarity_enabled: scarcity.is_some(),
        ..SearchStats::default()
    };
    let mut best: Option<CandidateResult> = None;

    for black_step in 0..=settings.max_extra_blacks {
        overall_stats.variants_tried += 1;
        let current_settings = SizeSettings {
            target_blacks: settings.target_blacks + black_step,
            step_time_budget_ms: step_time_budget_ms_override
                .unwrap_or(settings.step_time_budget_ms),
            outward_time_budget_ms: step_time_budget_ms_override
                .unwrap_or(settings.outward_time_budget_ms),
            ..settings
        };
        eprintln!(
            "black_step {} size={} target_blacks={} attempt_budget={} step_budget_ms={}",
            black_step,
            size,
            current_settings.target_blacks,
            current_settings.attempt_budget,
            current_settings.step_time_budget_ms,
        );
        let step_deadline = Instant::now()
            + std::time::Duration::from_millis(current_settings.step_time_budget_ms.max(1));

        let incremental_seed = template_seed(seed, black_step, 0, SALT_INCREMENTAL_BASE);
        let mut incremental_rng = StdRng::seed_from_u64(incremental_seed);
        let probe_nodes = (current_settings.max_nodes / 3).max(25_000);
        let min_solver_step = current_settings.target_blacks;
        let incremental_cancel = AtomicBool::new(false);
        let incremental_template = generate_incremental_template(
            size,
            &|template| {
                let slots = extract_slots(template);
                if template_ok(template, &slots, current_settings, size).is_err()
                    || !slot_capacity_ok(&slots, &index, current_settings)
                {
                    return false;
                }
                let mut probe_rng =
                    StdRng::seed_from_u64(template_seed(seed, black_step, 0, SALT_SOLVE_GRID_PROBE));
                solve_grid(
                    template,
                    &slots,
                    &index,
                    scarcity,
                    probe_nodes,
                    false,
                    &incremental_cancel,
                    Some(step_deadline),
                    &mut probe_rng,
                )
                .0
                .is_some()
            },
            current_settings.target_blacks,
            min_solver_step,
            &mut incremental_rng,
        );
        if incremental_template.is_some() {
            eprintln!("black_step {} incremental template ready", black_step);
        }

        let cancel = Arc::new(AtomicBool::new(false));
        let attempt_budget = current_settings.attempt_budget * preparation_attempts.max(1);
        let mut solved_in_step = 0usize;
        let mut attempt_offset = 0usize;
        let mut step_best: Option<CandidateResult> = None;
        while Instant::now() < step_deadline && step_best.is_none() {
            let results: Vec<AttemptOutcome> = (0..attempt_budget)
                .into_par_iter()
                .map(|attempt_idx| {
                    run_single_candidate(
                        size,
                        current_settings,
                        &index,
                        scarcity,
                        seed,
                        black_step,
                        attempt_offset + attempt_idx,
                        incremental_template.as_deref(),
                        cancel.as_ref(),
                        step_deadline,
                    )
                })
                .collect();
            attempt_offset += attempt_budget;
            overall_stats.templates_tried += attempt_budget;

            for outcome in results {
                combine_stats(&mut overall_stats, &outcome.stats);
                if let Some(result) = outcome.candidate {
                    solved_in_step += 1;
                    if step_best
                        .as_ref()
                        .is_none_or(|current| prefer_candidate(&result, current, seed))
                    {
                        step_best = Some(result);
                    }
                }
            }
        }
        overall_stats.failed_templates += attempt_offset.saturating_sub(solved_in_step);
        eprintln!(
            "black_step {} solved_candidates={} best_avg_len={}",
            black_step,
            solved_in_step,
            step_best
                .as_ref()
                .map(|candidate| candidate.report.average_length)
                .unwrap_or(0.0)
        );
        if let Some(step_best) = step_best {
            overall_stats.black_relaxation_steps = black_step;
            overall_stats.inward_elapsed_ms = inward_started_at.elapsed().as_millis();
            let inward_black_count = count_black_cells(&step_best.template);
            overall_stats.inward_chosen_black_count = inward_black_count;
            eprintln!(
                "inward grid size={} blacks={} edge_singletons={}\n{}",
                size,
                inward_black_count,
                step_best.stats.edge_singletons,
                render_candidate_grid(&step_best),
            );
            let optimized = if inward_black_count == 0 {
                overall_stats.outward_skipped_zero_black = true;
                eprintln!("outward skipped size={} reason=zero_blacks", size);
                step_best
            } else {
                let outward_deadline = Instant::now()
                    + std::time::Duration::from_millis(
                        current_settings.outward_time_budget_ms.max(1),
                    );
                let optimized = run_outward_phase(
                    size,
                    current_settings,
                    &index,
                    scarcity,
                    seed,
                    black_step,
                    step_best,
                    outward_deadline,
                    &mut overall_stats,
                );
                eprintln!(
                    "outward grid size={} blacks={} edge_singletons={}\n{}",
                    size,
                    count_black_cells(&optimized.template),
                    optimized.stats.edge_singletons,
                    render_candidate_grid(&optimized),
                );
                optimized
            };
            best = Some(optimized);
            break;
        }
        if Instant::now() >= step_deadline {
            overall_stats.budget_exhausted = true;
        }
    }

    let best = best.ok_or(EngineError::NoSolution(size))?;
    overall_stats.elapsed_ms = started_at.elapsed().as_millis();
    overall_stats.status = "solved".to_string();
    overall_stats.edge_singletons = best.stats.edge_singletons;
    overall_stats.chosen_black_count = count_black_cells(&best.template);

    let filled_grid = best
        .filled_grid
        .iter()
        .map(|row| {
            row.iter()
                .map(|cell| match cell {
                    Some('#') => None,
                    Some(ch) => Some(ch.to_string()),
                    None => None,
                })
                .collect()
        })
        .collect();
    Ok(EngineOutput {
        template: best.template.clone(),
        filled_grid,
        slots: best.slots.clone(),
        words: render_words(&best.slots, &best.assignment, &index)?,
        quality: best.report.clone(),
        stats: overall_stats,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dictionary_profile::{RuntimeDictionaryProfile, build_dictionary_profile};

    fn fake_candidate(
        template: Vec<Vec<bool>>,
        middle_window_average_length: f64,
        average_length: f64,
        edge_singletons: usize,
    ) -> CandidateResult {
        CandidateResult {
            report: QualityReport {
                score: 1000.0,
                word_count: 0,
                middle_window_average_length,
                average_length,
                average_rarity: 0.0,
                two_letter_words: 0,
                three_letter_words: 0,
                high_rarity_words: 0,
                uncommon_letter_words: 0,
                friendly_words: 0,
                max_rarity: 0,
                average_definability: 10.0,
            },
            template,
            filled_grid: vec![vec![Some('A'); 3]; 3],
            slots: Vec::new(),
            assignment: Vec::new(),
            stats: SearchStats {
                edge_singletons,
                ..SearchStats::default()
            },
        }
    }

    #[test]
    fn unsupported_size_returns_error() {
        let err = run_engine(6, "/tmp/missing.json", 1, 1, None).expect_err("unsupported size");
        assert!(matches!(err, EngineError::UnsupportedSize(6)));
    }

    #[test]
    fn settings_progress_monotonically() {
        let supported: Vec<SizeSettings> = (7..=15)
            .map(|size| settings_for_size(size).expect("supported size"))
            .collect();

        for pair in supported.windows(2) {
            let left = pair[0];
            let right = pair[1];
            assert!(right.target_blacks > left.target_blacks);
            assert!(right.attempt_budget > left.attempt_budget);
            assert!(right.template_attempts > left.template_attempts);
            assert!(right.step_time_budget_ms >= left.step_time_budget_ms);
            assert!(right.max_two_letter_slots >= left.max_two_letter_slots);
            assert!(right.min_candidates_per_slot <= left.min_candidates_per_slot);
            assert!(right.max_nodes > left.max_nodes);
        }
    }

    #[test]
    fn settings_use_exact_start_targets_and_15s_budget() {
        let expected = [
            (7, 0),
            (8, 1),
            (9, 4),
            (10, 7),
            (11, 10),
            (12, 15),
            (13, 18),
            (14, 23),
            (15, 31),
        ];

        for (size, target_blacks) in expected {
            let settings = settings_for_size(size).expect("supported size");
            assert_eq!(target_blacks, settings.target_blacks);
            assert_eq!(15_000, settings.step_time_budget_ms);
            assert_eq!(15_000, settings.outward_time_budget_ms);
        }
    }

    #[test]
    fn sparse_long_buckets_raise_effort_not_black_budget() {
        let mut raw_words = Vec::new();
        for idx in 0..80 {
            for length in 5..=8 {
                let ch = (b'A' + (idx % 26) as u8) as char;
                let normalized = format!("{ch}{}", "B".repeat(length - 1));
                raw_words.push(RawWord {
                    normalized,
                    original: String::new(),
                    rarity_level: Some(1),
                    length: Some(length),
                    word_type: None,
                    clue_support_score: None,
                    source: None,
                });
            }
        }
        for idx in 0..3 {
            let ch = (b'K' + idx as u8) as char;
            let normalized = format!("{ch}{}", "Q".repeat(14));
            raw_words.push(RawWord {
                normalized,
                original: String::new(),
                rarity_level: Some(1),
                length: Some(15),
                word_type: None,
                clue_support_score: None,
                source: None,
            });
        }

        let artifact = build_dictionary_profile(&raw_words, "words.json");
        let runtime = RuntimeDictionaryProfile::from_artifact(artifact);
        let profile = runtime.size(15).expect("size profile");
        let base = settings_for_size(15).expect("base");
        let (tuned, summary) = plan_search_effort(base, Some(profile));
        assert_eq!(tuned.target_blacks, base.target_blacks);
        assert_eq!(tuned.max_extra_blacks, base.max_extra_blacks);
        assert!(summary.dictionary_profile_loaded);
        assert!(tuned.min_candidates_per_slot <= base.min_candidates_per_slot);
        assert!(tuned.template_attempts >= base.template_attempts);
        assert!(tuned.max_nodes >= base.max_nodes);
    }

    #[test]
    fn same_black_count_prefers_fewer_edge_singletons() {
        let cleaner = fake_candidate(
            vec![
                vec![true, true, false],
                vec![true, true, true],
                vec![false, true, true],
            ],
            4.0,
            4.0,
            1,
        );
        let noisier = fake_candidate(
            vec![
                vec![true, false, true],
                vec![true, true, true],
                vec![true, false, true],
            ],
            6.0,
            6.0,
            3,
        );

        assert!(prefer_candidate(&cleaner, &noisier, 42));
        assert!(!prefer_candidate(&noisier, &cleaner, 42));
    }

    #[test]
    fn fewer_blacks_beats_same_quality_parent() {
        let parent = fake_candidate(
            vec![
                vec![true, false, true],
                vec![true, true, true],
                vec![true, false, true],
            ],
            5.0,
            5.0,
            1,
        );
        let child = fake_candidate(
            vec![
                vec![true, true, true],
                vec![true, true, true],
                vec![true, false, true],
            ],
            5.0,
            5.0,
            2,
        );

        assert!(better_with_fewer_blacks(&child, &parent, 7));
        assert!(!better_with_fewer_blacks(&parent, &child, 7));
    }

    #[test]
    fn outward_skips_zero_black_candidate() {
        let settings = settings_for_size(7).expect("supported size");
        let candidate = fake_candidate(vec![vec![true, true], vec![true, true]], 7.0, 7.0, 0);
        let index = WordIndex::new(&[]);
        let mut stats = SearchStats::default();

        let optimized = run_outward_phase(
            7,
            settings,
            &index,
            None,
            42,
            0,
            candidate.clone(),
            Instant::now() + std::time::Duration::from_millis(50),
            &mut stats,
        );

        assert_eq!(candidate.template, optimized.template);
        assert!(stats.outward_skipped_zero_black);
        assert_eq!(0, stats.outward_removal_attempts);
        assert_eq!(0, stats.outward_rounds);
    }

    #[test]
    fn higher_central_longness_beats_higher_average_length() {
        let central = fake_candidate(vec![vec![true, true]], 6.0, 5.0, 1);
        let average = fake_candidate(vec![vec![true, true]], 5.0, 7.0, 1);

        assert!(prefer_candidate(&central, &average, 42));
        assert!(!prefer_candidate(&average, &central, 42));
    }

    #[test]
    fn equal_central_longness_uses_average_length_as_tiebreak() {
        let longer = fake_candidate(vec![vec![true, true]], 5.5, 6.0, 1);
        let shorter = fake_candidate(vec![vec![true, true]], 5.5, 4.0, 1);

        assert!(prefer_candidate(&longer, &shorter, 42));
        assert!(!prefer_candidate(&shorter, &longer, 42));
    }
}
