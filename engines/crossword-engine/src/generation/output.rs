use std::fmt::{Display, Formatter};

use serde::Serialize;

use crate::quality::QualityReport;
use crate::slots::Slot;

#[derive(Clone, Debug, Default, Serialize)]
pub struct SearchStats {
    pub elapsed_ms: u128,
    pub inward_elapsed_ms: u128,
    pub outward_elapsed_ms: u128,
    pub base_target_blacks: usize,
    pub variants_tried: usize,
    pub templates_tried: usize,
    pub solved_candidates: usize,
    pub failed_templates: usize,
    pub solver_nodes: usize,
    pub status: String,
    pub inward_chosen_black_count: usize,
    pub chosen_black_count: usize,
    pub outward_rounds: usize,
    pub outward_skipped_zero_black: bool,
    pub black_relaxation_steps: usize,
    pub dictionary_total_rows: usize,
    pub dictionary_unique_words: usize,
    pub dictionary_duplicate_rows: usize,
    pub dictionary_skipped_rows: usize,
    pub dictionary_profile_loaded: bool,
    pub dictionary_profile_medium_density: f64,
    pub dictionary_profile_long_density: f64,
    pub dictionary_profile_density_gap: f64,
    pub effective_max_nodes: usize,
    pub effective_template_attempts: usize,
    pub effective_min_candidates_per_slot: usize,
    pub budget_exhausted: bool,
    pub edge_singletons: usize,
    pub inward_solutions_found: usize,
    pub outward_removal_attempts: usize,
    pub outward_removal_successes: usize,
    pub positional_rarity_enabled: bool,
    pub rejected_spacing: usize,
    pub rejected_singleton_interior: usize,
    pub rejected_disconnected: usize,
    pub rejected_uncovered_white: usize,
    pub rejected_slot_capacity: usize,
    pub rejected_solver_unsat: usize,
    pub rejected_solver_timeout: usize,
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
                write!(
                    f,
                    "could not generate a valid filled grid for {size}x{size}"
                )
            }
            Self::InternalInvariant(msg) => write!(f, "internal invariant failed: {msg}"),
        }
    }
}

impl std::error::Error for EngineError {}
