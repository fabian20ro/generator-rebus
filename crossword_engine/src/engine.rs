use std::fs;
use std::time::Instant;

use rand::Rng;
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
    solved_candidates: usize,
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
}

#[derive(Clone, Debug)]
struct CandidateResult {
    score: f64,
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
    pub original: String,
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

fn settings_for_size(size: usize) -> SizeSettings {
    match size {
        7 => SizeSettings {
            max_nodes: 250_000,
            target_blacks: 6,
            solved_candidates: 6,
            attempt_budget: 40,
            max_two_letter_slots: 4,
            min_candidates_per_slot: 16,
            template_attempts: 300,
            max_full_width_slots: None,
        },
        8 => SizeSettings {
            max_nodes: 400_000,
            target_blacks: 8,
            solved_candidates: 6,
            attempt_budget: 50,
            max_two_letter_slots: 6,
            min_candidates_per_slot: 16,
            template_attempts: 450,
            max_full_width_slots: None,
        },
        9 => SizeSettings {
            max_nodes: 700_000,
            target_blacks: 11,
            solved_candidates: 5,
            attempt_budget: 60,
            max_two_letter_slots: 8,
            min_candidates_per_slot: 18,
            template_attempts: 650,
            max_full_width_slots: None,
        },
        10 => SizeSettings {
            max_nodes: 1_000_000,
            target_blacks: 16,
            solved_candidates: 5,
            attempt_budget: 70,
            max_two_letter_slots: 10,
            min_candidates_per_slot: 18,
            template_attempts: 850,
            max_full_width_slots: None,
        },
        11 => SizeSettings {
            max_nodes: 1_500_000,
            target_blacks: 16,
            solved_candidates: 4,
            attempt_budget: 80,
            max_two_letter_slots: 18,
            min_candidates_per_slot: 14,
            template_attempts: 1_000,
            max_full_width_slots: Some(5),
        },
        12 => SizeSettings {
            max_nodes: 2_000_000,
            target_blacks: 20,
            solved_candidates: 4,
            attempt_budget: 90,
            max_two_letter_slots: 22,
            min_candidates_per_slot: 10,
            template_attempts: 1_200,
            max_full_width_slots: Some(5),
        },
        13 => SizeSettings {
            max_nodes: 2_800_000,
            target_blacks: 28,
            solved_candidates: 4,
            attempt_budget: 100,
            max_two_letter_slots: 30,
            min_candidates_per_slot: 8,
            template_attempts: 1_500,
            max_full_width_slots: Some(6),
        },
        14 => SizeSettings {
            max_nodes: 3_800_000,
            target_blacks: 40,
            solved_candidates: 3,
            attempt_budget: 110,
            max_two_letter_slots: 40,
            min_candidates_per_slot: 6,
            template_attempts: 1_850,
            max_full_width_slots: Some(7),
        },
        15 => SizeSettings {
            max_nodes: 5_000_000,
            target_blacks: 60,
            solved_candidates: 3,
            attempt_budget: 70,
            max_two_letter_slots: 50,
            min_candidates_per_slot: 4,
            template_attempts: 2_200,
            max_full_width_slots: Some(8),
        },
        _ => panic!("Unsupported size {size}"),
    }
}

fn build_relaxed_variants(size: usize) -> [SizeSettings; 3] {
    let base = settings_for_size(size);
    [
        base,
        SizeSettings {
            max_nodes: base.max_nodes * 2,
            target_blacks: base.target_blacks + 2,
            solved_candidates: base.solved_candidates,
            attempt_budget: base.attempt_budget + 20,
            max_two_letter_slots: base.max_two_letter_slots + 2,
            min_candidates_per_slot: base.min_candidates_per_slot.saturating_sub(4).max(8),
            template_attempts: base.template_attempts + 150,
            max_full_width_slots: base.max_full_width_slots,
        },
        SizeSettings {
            max_nodes: base.max_nodes * 3,
            target_blacks: base.target_blacks + 4,
            solved_candidates: base.solved_candidates.saturating_sub(1).max(3),
            attempt_budget: base.attempt_budget + 35,
            max_two_letter_slots: base.max_two_letter_slots
                + if base.max_two_letter_slots >= 16 {
                    6
                } else {
                    4
                },
            min_candidates_per_slot: base.min_candidates_per_slot.saturating_sub(8).max(6),
            template_attempts: base.template_attempts + 250,
            max_full_width_slots: base.max_full_width_slots,
        },
    ]
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

fn template_seed(base_seed: u64, variant_idx: usize, attempt_idx: usize, salt: u64) -> u64 {
    base_seed
        ^ ((variant_idx as u64 + 1) << 48)
        ^ ((attempt_idx as u64 + 1).wrapping_mul(0x9E37_79B9_7F4A_7C15))
        ^ salt
}

fn render_words(slots: &[Slot], assignment: &[usize], index: &WordIndex) -> Vec<OutputWord> {
    slots
        .iter()
        .map(|slot| {
            let bucket = index.bucket(slot.length).expect("bucket");
            let word = &bucket.words[assignment[slot.id]];
            OutputWord {
                slot_id: slot.id,
                normalized: word.normalized.clone(),
                original: word.original.clone(),
            }
        })
        .collect()
}

fn run_single_candidate(
    size: usize,
    settings: SizeSettings,
    index: &WordIndex,
    base_seed: u64,
    variant_idx: usize,
    attempt_idx: usize,
    incremental_template: Option<&[Vec<bool>]>,
) -> Option<CandidateResult> {
    let mut stats = SearchStats {
        templates_tried: 1,
        ..SearchStats::default()
    };
    let mut rng =
        StdRng::seed_from_u64(template_seed(base_seed, variant_idx, attempt_idx, 0xA11CE));
    let template = if let Some(template) = incremental_template {
        template.to_vec()
    } else {
        let choices = [-2isize, -1, 0, 1, 2];
        let delta = choices[rng.random_range(0..choices.len())];
        let target = settings.target_blacks.saturating_add_signed(delta).max(1);
        generate_procedural_template(size, target, settings.template_attempts, &mut rng)?
    };
    let slots = extract_slots(&template);
    if !template_ok(&template, &slots, settings, size) || !slot_capacity_ok(&slots, index, settings)
    {
        return None;
    }

    let allow_reuse = size >= 15;
    let solve_start = Instant::now();
    let (assignment, filled_grid, solve_stats) = solve_grid(
        &template,
        &slots,
        index,
        settings.max_nodes,
        allow_reuse,
        &mut rng,
    )?;
    let _solve_elapsed = solve_start.elapsed();
    stats.solved_candidates = 1;
    stats.solver_nodes = solve_stats.nodes;
    let solved_words: Vec<&WordEntry> = slots
        .iter()
        .map(|slot| {
            let bucket = index.bucket(slot.length).expect("bucket");
            &bucket.words[assignment[slot.id].expect("assignment")]
        })
        .collect();
    let report = score_words(&solved_words, size);
    Some(CandidateResult {
        score: report.score,
        report,
        template,
        filled_grid,
        slots,
        assignment: assignment
            .into_iter()
            .map(|idx| idx.expect("assigned"))
            .collect(),
        stats,
    })
}

fn combine_stats(left: &mut SearchStats, right: &SearchStats) {
    left.variants_tried += right.variants_tried;
    left.templates_tried += right.templates_tried;
    left.solved_candidates += right.solved_candidates;
    left.failed_templates += right.failed_templates;
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
) -> Result<EngineOutput, String> {
    let started_at = Instant::now();
    let raw_words: Vec<RawWord> = serde_json::from_str(
        &fs::read_to_string(words_path).map_err(|err| format!("read words: {err}"))?,
    )
    .map_err(|err| format!("parse words: {err}"))?;
    let filtered_words = filter_word_records(&raw_words, size);
    let index = WordIndex::new(&filtered_words);
    let mut overall_stats = SearchStats::default();
    let mut best: Option<CandidateResult> = None;

    for (variant_idx, settings) in build_relaxed_variants(size).into_iter().enumerate() {
        overall_stats.variants_tried += 1;
        eprintln!(
            "variant {} size={} target_blacks={} attempt_budget={}",
            variant_idx + 1,
            size,
            settings.target_blacks,
            settings.attempt_budget
        );

        let incremental_seed = template_seed(seed, variant_idx, 0, 0x1CE0_0001);
        let mut incremental_rng = StdRng::seed_from_u64(incremental_seed);
        let probe_nodes = (settings.max_nodes / 3).max(25_000);
        let effective_max = settings.target_blacks + 4;
        let min_solver_step = effective_max.saturating_sub(6).max(1);
        let incremental_template = generate_incremental_template(
            size,
            &|template| {
                let slots = extract_slots(template);
                if !template_ok(template, &slots, settings, size)
                    || !slot_capacity_ok(&slots, &index, settings)
                {
                    return false;
                }
                let mut probe_rng =
                    StdRng::seed_from_u64(template_seed(seed, variant_idx, 0, 0x0BEE_F123));
                solve_grid(
                    template,
                    &slots,
                    &index,
                    probe_nodes,
                    size >= 15,
                    &mut probe_rng,
                )
                .is_some()
            },
            effective_max,
            min_solver_step,
            &mut incremental_rng,
        );
        if incremental_template.is_some() {
            eprintln!("variant {} incremental template ready", variant_idx + 1);
        }

        let attempt_budget = settings.attempt_budget * preparation_attempts.max(1);
        let results: Vec<Option<CandidateResult>> = (0..attempt_budget)
            .into_par_iter()
            .map(|attempt_idx| {
                run_single_candidate(
                    size,
                    settings,
                    &index,
                    seed,
                    variant_idx,
                    attempt_idx,
                    incremental_template.as_deref(),
                )
            })
            .collect();

        let mut solved_in_variant = 0usize;
        for result in results.into_iter().flatten() {
            solved_in_variant += 1;
            combine_stats(&mut overall_stats, &result.stats);
            if best
                .as_ref()
                .is_none_or(|current| result.score > current.score)
            {
                best = Some(result);
            }
        }
        overall_stats.templates_tried += attempt_budget.saturating_sub(solved_in_variant);
        overall_stats.failed_templates += attempt_budget.saturating_sub(solved_in_variant);
        eprintln!(
            "variant {} solved_candidates={} best_score={}",
            variant_idx + 1,
            solved_in_variant,
            best.as_ref()
                .map(|candidate| candidate.score)
                .unwrap_or(0.0)
        );
        if solved_in_variant >= settings.solved_candidates && best.is_some() {
            break;
        }
    }

    let best =
        best.ok_or_else(|| format!("Could not generate a valid filled grid for {size}x{size}"))?;
    overall_stats.elapsed_ms = started_at.elapsed().as_millis();
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
        words: render_words(&best.slots, &best.assignment, &index),
        quality: best.report.clone(),
        stats: overall_stats,
    })
}
