use crate::dictionary_profile::RuntimeSizeDictionaryProfile;

#[derive(Clone, Copy, Debug)]
pub(crate) struct SizeSettings {
    pub max_nodes: usize,
    pub target_blacks: usize,
    pub max_extra_blacks: usize,
    pub attempt_budget: usize,
    pub step_time_budget_ms: u64,
    pub outward_time_budget_ms: u64,
    pub outward_beam_width: usize,
    pub max_two_letter_slots: usize,
    pub min_candidates_per_slot: usize,
    pub template_attempts: usize,
    pub max_full_width_slots: Option<usize>,
}

#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct EffortPlanningSummary {
    pub dictionary_profile_loaded: bool,
    pub medium_density: f64,
    pub long_density: f64,
    pub density_gap: f64,
}

pub(crate) fn settings_for_size(size: usize) -> Option<SizeSettings> {
    if !(7..=15).contains(&size) {
        return None;
    }

    let step = (size - 7) as f64;
    let max_nodes = round_to(600_000.0 * 1.45_f64.powf(step), 50_000.0);
    let target_blacks = match size {
        7 => 0,
        8 => 2,
        9 => 5,
        10 => 8,
        11 => 11,
        12 => 16,
        13 => 20,
        14 => 28,
        15 => 34,
        _ => unreachable!("validated size range"),
    };
    let max_extra_blacks = match size {
        7..=10 => 4,
        11..=12 => 5,
        13..=14 => 6,
        _ => 8,
    };
    let attempt_budget = 48 + 6 * (size - 7) + (3 * (size - 7) * (size - 7)) / 4;
    let step_time_budget_ms = 15_000;
    let outward_beam_width = match size {
        7..=10 => 4,
        11..=12 => 6,
        _ => 8,
    };
    let max_two_letter_slots = (2.0 + 0.25 * step + 0.25 * step * step).round() as usize;
    let min_candidates_per_slot = ((18.0 - 1.25 * step).round() as usize).max(8);
    let template_attempts = round_to(500.0 + 125.0 * step + 40.0 * step * step, 50.0).max(500);

    Some(SizeSettings {
        max_nodes,
        target_blacks,
        max_extra_blacks,
        attempt_budget,
        step_time_budget_ms,
        outward_time_budget_ms: step_time_budget_ms,
        outward_beam_width,
        max_two_letter_slots,
        min_candidates_per_slot,
        template_attempts,
        max_full_width_slots: None,
    })
}

pub(crate) fn plan_search_effort(
    base: SizeSettings,
    profile: Option<&RuntimeSizeDictionaryProfile>,
) -> (SizeSettings, EffortPlanningSummary) {
    let Some(profile) = profile else {
        return (base, EffortPlanningSummary::default());
    };

    let density_gap = profile.density_gap.max(0.0);
    let candidate_penalty = (density_gap * 1.5).round() as usize;
    let node_scale = 1.0 + density_gap * 0.10;
    let template_scale = 1.0 + density_gap * 0.08;
    let tuned = SizeSettings {
        max_nodes: round_to(base.max_nodes as f64 * node_scale, 50_000.0).max(base.max_nodes),
        target_blacks: base.target_blacks,
        max_extra_blacks: base.max_extra_blacks,
        attempt_budget: base.attempt_budget,
        step_time_budget_ms: base.step_time_budget_ms,
        outward_time_budget_ms: base.outward_time_budget_ms,
        outward_beam_width: base.outward_beam_width,
        max_two_letter_slots: base.max_two_letter_slots,
        min_candidates_per_slot: base
            .min_candidates_per_slot
            .saturating_sub(candidate_penalty)
            .max(6),
        template_attempts: round_to(base.template_attempts as f64 * template_scale, 50.0)
            .max(base.template_attempts),
        max_full_width_slots: base.max_full_width_slots,
    };
    (
        tuned,
        EffortPlanningSummary {
            dictionary_profile_loaded: true,
            medium_density: profile.medium_density,
            long_density: profile.long_density,
            density_gap,
        },
    )
}

fn round_to(value: f64, quantum: f64) -> usize {
    ((value / quantum).round() * quantum) as usize
}
