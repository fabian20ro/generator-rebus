use crate::words::WordEntry;

#[derive(Clone, Copy, Debug)]
pub(crate) struct SizeSettings {
    pub max_nodes: usize,
    pub target_blacks: usize,
    pub max_extra_blacks: usize,
    pub attempt_budget: usize,
    pub max_two_letter_slots: usize,
    pub min_candidates_per_slot: usize,
    pub template_attempts: usize,
    pub max_full_width_slots: Option<usize>,
}

pub(crate) fn settings_for_size(size: usize) -> Option<SizeSettings> {
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

pub(crate) fn tune_settings_for_dictionary(
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
