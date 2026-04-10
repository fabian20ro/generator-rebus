use std::cmp::Ordering;
use std::fs;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::time::Instant;

use rand::SeedableRng;
use rand::rngs::StdRng;
use rayon::prelude::*;

use super::candidate::CandidateResult;
use super::output::{EngineError, EngineOutput, OutputWord, SearchStats};
use super::settings::{SizeSettings, settings_for_size, tune_settings_for_dictionary};
use crate::quality::{QualityReport, score_words};
use crate::slots::{Slot, extract_slots};
use crate::solver::{WordIndex, solve_grid};
use crate::template::{
    generate_incremental_template, generate_procedural_template, validate_template,
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
) -> bool {
    if validate_template(template).is_err() {
        return false;
    }
    if count_two_letter_slots(template) > settings.max_two_letter_slots {
        return false;
    }
    if let Some(limit) = settings.max_full_width_slots {
        let full_width = slots.iter().filter(|slot| slot.length == size).count();
        if full_width > limit {
            return false;
        }
    }
    true
}

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
        });
    }
    Ok(rendered)
}

fn candidate_better(candidate: &CandidateResult, current: &CandidateResult) -> bool {
    let candidate_blacks = count_black_cells(&candidate.template);
    let current_blacks = count_black_cells(&current.template);
    let candidate_key = (
        candidate.report.average_length,
        ReverseOrd(candidate_blacks as f64),
        ReverseOrd(candidate.report.two_letter_words as f64),
        ReverseOrd(candidate.report.three_letter_words as f64),
        candidate.report.average_definability,
        ReverseOrd(candidate.report.uncommon_letter_words as f64),
    );
    let current_key = (
        current.report.average_length,
        ReverseOrd(current_blacks as f64),
        ReverseOrd(current.report.two_letter_words as f64),
        ReverseOrd(current.report.three_letter_words as f64),
        current.report.average_definability,
        ReverseOrd(current.report.uncommon_letter_words as f64),
    );
    compare_rank(candidate_key, current_key) == Ordering::Greater
}

#[derive(Clone, Copy)]
struct ReverseOrd(f64);

fn compare_f64(left: f64, right: f64) -> Ordering {
    left.partial_cmp(&right).unwrap_or(Ordering::Equal)
}

fn compare_rank(
    left: (f64, ReverseOrd, ReverseOrd, ReverseOrd, f64, ReverseOrd),
    right: (f64, ReverseOrd, ReverseOrd, ReverseOrd, f64, ReverseOrd),
) -> Ordering {
    compare_f64(left.0, right.0)
        .then_with(|| compare_f64(right.1.0, left.1.0))
        .then_with(|| compare_f64(right.2.0, left.2.0))
        .then_with(|| compare_f64(right.3.0, left.3.0))
        .then_with(|| compare_f64(left.4, right.4))
        .then_with(|| compare_f64(right.5.0, left.5.0))
}

fn run_single_candidate(
    size: usize,
    settings: SizeSettings,
    index: &WordIndex,
    base_seed: u64,
    black_step: usize,
    attempt_idx: usize,
    incremental_template: Option<&[Vec<bool>]>,
    cancel: &AtomicBool,
) -> Option<CandidateResult> {
    if cancel.load(AtomicOrdering::Relaxed) {
        return None;
    }

    let mut stats = SearchStats::default();
    let mut rng = StdRng::seed_from_u64(template_seed(base_seed, black_step, attempt_idx, 0xA11CE));
    let template = if let Some(template) = incremental_template {
        template.to_vec()
    } else {
        generate_procedural_template(size, settings.target_blacks, settings.template_attempts, &mut rng)?
    };
    let slots = extract_slots(&template);
    if !template_ok(&template, &slots, settings, size) || !slot_capacity_ok(&slots, index, settings)
    {
        return None;
    }

    let (assignment, filled_grid, solve_stats) = solve_grid(
        &template,
        &slots,
        index,
        settings.max_nodes,
        false,
        cancel,
        &mut rng,
    )?;

    let mut solved_words = Vec::with_capacity(slots.len());
    let mut flat_assignment = Vec::with_capacity(slots.len());
    for slot in &slots {
        let word_idx = assignment.get(slot.id).and_then(|value| *value)?;
        let bucket = index.bucket(slot.length)?;
        let word = bucket.words.get(word_idx)?;
        solved_words.push(word);
        flat_assignment.push(word_idx);
    }
    let report = score_words(&solved_words, size);
    stats.solved_candidates = 1;
    stats.solver_nodes = solve_stats.nodes;
    cancel.store(true, AtomicOrdering::Relaxed);
    Some(CandidateResult {
        report,
        template,
        filled_grid,
        slots,
        assignment: flat_assignment,
        stats,
    })
}

fn combine_stats(left: &mut SearchStats, right: &SearchStats) {
    left.variants_tried += right.variants_tried;
    left.solved_candidates += right.solved_candidates;
    left.solver_nodes += right.solver_nodes;
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

pub fn run_engine(
    size: usize,
    words_path: &str,
    seed: u64,
    preparation_attempts: usize,
) -> Result<EngineOutput, EngineError> {
    let started_at = Instant::now();
    let base_settings = settings_for_size(size).ok_or(EngineError::UnsupportedSize(size))?;
    let raw_words: Vec<RawWord> = serde_json::from_str(
        &fs::read_to_string(words_path).map_err(EngineError::ReadWords)?,
    )
    .map_err(EngineError::ParseWords)?;
    let (filtered_words, dictionary_stats) = filter_word_records(&raw_words, size);
    if filtered_words.is_empty() {
        return Err(EngineError::NoUsableWords(size));
    }
    let settings = tune_settings_for_dictionary(base_settings, size, &filtered_words);
    let index = WordIndex::new(&filtered_words);
    let mut overall_stats = SearchStats {
        dictionary_total_rows: dictionary_stats.total_rows,
        dictionary_unique_words: dictionary_stats.unique_words,
        dictionary_duplicate_rows: dictionary_stats.duplicate_rows,
        dictionary_skipped_rows: dictionary_stats.skipped_rows,
        ..SearchStats::default()
    };
    let mut best: Option<CandidateResult> = None;

    for black_step in 0..=settings.max_extra_blacks {
        overall_stats.variants_tried += 1;
        let current_settings = SizeSettings {
            target_blacks: settings.target_blacks + black_step,
            ..settings
        };
        eprintln!(
            "black_step {} size={} target_blacks={} attempt_budget={}",
            black_step,
            size,
            current_settings.target_blacks,
            current_settings.attempt_budget
        );

        let incremental_seed = template_seed(seed, black_step, 0, 0x1CE0_0001);
        let mut incremental_rng = StdRng::seed_from_u64(incremental_seed);
        let probe_nodes = (current_settings.max_nodes / 3).max(25_000);
        let min_solver_step = current_settings.target_blacks;
        let incremental_cancel = AtomicBool::new(false);
        let incremental_template = generate_incremental_template(
            size,
            &|template| {
                let slots = extract_slots(template);
                if !template_ok(template, &slots, current_settings, size)
                    || !slot_capacity_ok(&slots, &index, current_settings)
                {
                    return false;
                }
                let mut probe_rng =
                    StdRng::seed_from_u64(template_seed(seed, black_step, 0, 0x0BEE_F123));
                solve_grid(
                    template,
                    &slots,
                    &index,
                    probe_nodes,
                    false,
                    &incremental_cancel,
                    &mut probe_rng,
                )
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
        let results: Vec<Option<CandidateResult>> = (0..attempt_budget)
            .into_par_iter()
            .map(|attempt_idx| {
                run_single_candidate(
                    size,
                    current_settings,
                    &index,
                    seed,
                    black_step,
                    attempt_idx,
                    incremental_template.as_deref(),
                    cancel.as_ref(),
                )
            })
            .collect();

        let mut solved_in_step = 0usize;
        for result in results.into_iter().flatten() {
            solved_in_step += 1;
            combine_stats(&mut overall_stats, &result.stats);
            if best.as_ref().is_none_or(|current| candidate_better(&result, current)) {
                best = Some(result);
            }
        }
        overall_stats.templates_tried += attempt_budget;
        overall_stats.failed_templates += attempt_budget.saturating_sub(solved_in_step);
        eprintln!(
            "black_step {} solved_candidates={} best_avg_len={}",
            black_step,
            solved_in_step,
            best.as_ref()
                .map(|candidate| candidate.report.average_length)
                .unwrap_or(0.0)
        );
        if best.is_some() {
            overall_stats.black_relaxation_steps = black_step;
            break;
        }
    }

    let best = best.ok_or(EngineError::NoSolution(size))?;
    overall_stats.elapsed_ms = started_at.elapsed().as_millis();
    overall_stats.status = "solved".to_string();
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
    use crate::words::filter_word_records;

    #[test]
    fn unsupported_size_returns_error() {
        let err = run_engine(6, "/tmp/missing.json", 1, 1).expect_err("unsupported size");
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
            assert!(right.max_two_letter_slots >= left.max_two_letter_slots);
            assert!(right.min_candidates_per_slot <= left.min_candidates_per_slot);
            assert!(right.max_nodes > left.max_nodes);
        }
    }

    #[test]
    fn sparse_long_buckets_raise_black_budget() {
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
            });
        }

        let filtered = filter_word_records(&raw_words, 15).0;
        let base = settings_for_size(15).expect("base");
        let tuned = tune_settings_for_dictionary(base, 15, &filtered);
        assert!(tuned.target_blacks > base.target_blacks);
        assert!(tuned.max_extra_blacks >= base.max_extra_blacks);
        assert!(tuned.min_candidates_per_slot <= base.min_candidates_per_slot);
    }
}
