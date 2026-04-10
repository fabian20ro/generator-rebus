use std::fmt::{Display, Formatter};

use serde::Serialize;

use crate::slots::Slot;
use crate::quality::QualityReport;

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
