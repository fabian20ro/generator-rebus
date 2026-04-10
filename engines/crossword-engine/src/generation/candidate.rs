use crate::quality::QualityReport;
use crate::slots::Slot;

use super::output::SearchStats;

#[derive(Clone, Debug)]
pub(crate) struct CandidateResult {
    pub report: QualityReport,
    pub template: Vec<Vec<bool>>,
    pub filled_grid: Vec<Vec<Option<char>>>,
    pub slots: Vec<Slot>,
    pub assignment: Vec<usize>,
    pub stats: SearchStats,
}
