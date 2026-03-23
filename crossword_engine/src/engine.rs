use std::cmp::Ordering;
use std::fmt::{Display, Formatter};
use std::fs;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::time::Instant;

use rand::SeedableRng;
use rand::rngs::StdRng;
use rayon::prelude::*;
use serde::Serialize;

use crate::quality::{QualityReport, score_words};
use crate::slots::{Slot, extract_slots};
use crate::solver::{WordIndex, solve_grid};
use crate::template::{
    generate_incremental_template, generate_procedural_template, validate_template,
};
use crate::words::{RawWord, WordEntry, filter_word_records};

#[derive(Clone, Copy, Debug)]
struct SizeSettings {
    max_nodes: usize,
    target_blacks: usize,
    max_extra_blacks: usize,
    attempt_budget: usize,
    max_two_letter_slots: usize,
    min_candidates_per_slot: usize,
    template_attempts: usize,
    max_full_width_slots: Option<usize>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct SearchStats {
    pub elapsed_ms: u128,
    pub variants_tried: usize,
    pub templates_tried: usize,
    pub solved_candidates: usize,
    pub failed_templates: usize,
    pub solver_nodes: usize,
    pub status: String,
    pub chosen_black_count: usize,
    pub black_relaxation_steps: usize,
    pub dictionary_total_rows: usize,
    pub dictionary_unique_words: usize,
    pub dictionary_duplicate_rows: usize,
    pub dictionary_skipped_rows: usize,
    pub budget_exhausted: bool,
}

#[derive(Clone, Debug)]
struct CandidateResult {
    report: QualityReport,
    template: Vec<Vec<bool>>,
    filled_grid: Vec<Vec<Option<char>>>,
    slots: Vec<Slot>,
    assignment: Vec<usize>,
    stats: SearchStats,
}

#[derive(Debug, Serialize)]
pub struct OutputWord {
    pub slot_id: usize,
    pub normalized: String,
}

#[derive(Debug, Serialize)]
pub struct EngineOutput {
    pub template: Vec<Vec<bool>>,
    pub filled_grid: Vec<Vec<Option<String>>>,
    pub slots: Vec<Slot>,
    pub words: Vec<OutputWord>,
    pub quality: QualityReport,
    pub stats: SearchStats,
}

#[derive(Debug)]
pub enum EngineError {
    UnsupportedSize(usize),
    ReadWords(std::io::Error),
    ParseWords(serde_json::Error),
    NoUsableWords(usize),
    NoSolution(usize),
    InternalInvariant(&'static str),
}

impl Display for EngineError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnsupportedSize(size) => write!(f, "unsupported size: {size}"),
            Self::ReadWords(err) => write!(f, "read words: {err}"),
            Self::ParseWords(err) => write!(f, "parse words: {err}"),
            Self::NoUsableWords(size) => {
                write!(f, "no usable normalized words available for {size}x{size}")
            }
            Self::NoSolution(size) => {
                write!(f, "could not generate a valid filled grid for {size}x{size}")
            }
            Self::InternalInvariant(msg) => write!(f, "internal invariant failed: {msg}"),
        }
    }
}

impl std::error::Error for EngineError {}

fn settings_for_size(size: usize) -> Option<SizeSettings> {
    if !(7..=15).contains(&size) {
        return None;
    }

    let step = (size - 7) as f64;
    let area = (size * size) as f64;

    let target_black_density = 0.103 + 0.0015 * step + 0.001 * step * step;
    let max_nodes = round_to(600_000.0 * 1.45_f64.powf(step), 50_000.0);
    let target_blacks = (area * target_black_density).round() as usize;
    let max_extra_blacks = match size {
        7..=10 => 4,
        11..=12 => 5,
        13..=14 => 6,
        _ => 8,
    };
    let attempt_budget = 48 + 6 * (size - 7) + (3 * (size - 7) * (size - 7)) / 4;
    let max_two_letter_slots = (2.0 + 0.25 * step + 0.25 * step * step).round() as usize;
    let min_candidates_per_slot = ((18.0 - 1.25 * step).round() as usize).max(8);
    let template_attempts =
        round_to(500.0 + 125.0 * step + 40.0 * step * step, 50.0).max(500);

    Some(SizeSettings {
        max_nodes,
        target_blacks,
        max_extra_blacks,
        attempt_budget,
        max_two_letter_slots,
        min_candidates_per_slot,
        template_attempts,
        max_full_width_slots: None,
    })
}

fn round_to(value: f64, quantum: f64) -> usize {
    ((value / quantum).round() * quantum) as usize
}

fn average_log_bucket_density(counts: &[usize], lengths: std::ops::RangeInclusive<usize>) -> f64 {
    let mut total = 0.0f64;
    let mut seen = 0usize;
    for length in lengths {
        if let Some(count) = counts.get(length) {
            total += (*count as f64 + 1.0).ln();
            seen += 1;
        }
    }
    if seen == 0 {
        0.0
    } else {
        total / seen as f64
    }
}

fn tune_settings_for_dictionary(
    base: SizeSettings,
    size: usize,
    filtered_words: &[WordEntry],
) -> SizeSettings {
    let mut counts = vec![0usize; size + 1];
    for word in filtered_words {
        if word.len() <= size {
            counts[word.len()] += 1;
        }
    }

    let medium_density = average_log_bucket_density(&counts, 5..=size.min(8));
    let long_density =
        average_log_bucket_density(&counts, size.saturating_sub(2).max(5)..=size);
    let density_gap = (medium_density - long_density).max(0.0);
    if density_gap <= 0.0 {
        return base;
    }

    let black_bonus = (density_gap * 2.0).round() as usize;
    let extra_black_bonus = density_gap.ceil() as usize;
    let candidate_penalty = (density_gap * 1.5).round() as usize;
    let node_scale = 1.0 + density_gap * 0.10;
    let template_scale = 1.0 + density_gap * 0.08;

    SizeSettings {
        max_nodes: round_to(base.max_nodes as f64 * node_scale, 50_000.0).max(base.max_nodes),
        target_blacks: base.target_blacks + black_bonus,
        max_extra_blacks: base.max_extra_blacks + extra_black_bonus,
        attempt_budget: base.attempt_budget,
        max_two_letter_slots: base.max_two_letter_slots + black_bonus,
        min_candidates_per_slot: base
            .min_candidates_per_slot
            .saturating_sub(candidate_penalty)
            .max(6),
        template_attempts: round_to(base.template_attempts as f64 * template_scale, 50.0)
            .max(base.template_attempts),
        max_full_width_slots: base.max_full_width_slots,
    }
}

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
