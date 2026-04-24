use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

use rand::Rng;
use rand::seq::SliceRandom;

use crate::dictionary_profile::RuntimeSizeDictionaryProfile;
use crate::slots::Slot;
use std::collections::HashMap;

use super::{BitSet, WordIndex};

#[derive(Clone, Debug, Default)]
pub struct SolveStats {
    pub nodes: usize,
    pub timed_out: bool,
    pub node_limit_hit: bool,
}

struct SolveState<'a> {
    slots: &'a [Slot],
    index: &'a WordIndex,
    scarcity: Option<&'a RuntimeSizeDictionaryProfile>,
    grid: Vec<Vec<Option<char>>>,
    assignment: Vec<Option<usize>>,
    used_by_length: HashMap<usize, BitSet>,
    cell_use_count: Vec<Vec<usize>>,
    allow_reuse: bool,
}

impl<'a> SolveState<'a> {
    fn pattern_for_slot(&self, slot: &Slot) -> Vec<Option<char>> {
        slot.cells.iter().map(|(r, c)| self.grid[*r][*c]).collect()
    }

    fn exclude_for_length(&self, length: usize) -> Option<&BitSet> {
        if self.allow_reuse {
            None
        } else {
            self.used_by_length.get(&length)
        }
    }

    fn assign_word(&mut self, slot_id: usize, word_idx: usize) -> bool {
        let slot = &self.slots[slot_id];
        let Some(bucket) = self.index.bucket(slot.length) else {
            return false;
        };
        let word = &bucket.words[word_idx];
        self.assignment[slot_id] = Some(word_idx);
        if !self.allow_reuse {
            self.used_by_length
                .entry(slot.length)
                .or_insert_with(|| BitSet::empty(bucket.words.len()))
                .set(word_idx);
        }
        for (pos, (r, c)) in slot.cells.iter().enumerate() {
            self.grid[*r][*c] = Some(word.chars[pos]);
            self.cell_use_count[*r][*c] += 1;
        }
        true
    }

    fn unassign_word(&mut self, slot_id: usize, word_idx: usize) {
        let slot = &self.slots[slot_id];
        if !self.allow_reuse {
            if let Some(mask) = self.used_by_length.get_mut(&slot.length) {
                mask.clear(word_idx);
            }
        }
        self.assignment[slot_id] = None;
        for (r, c) in &slot.cells {
            self.cell_use_count[*r][*c] -= 1;
            if self.cell_use_count[*r][*c] == 0 {
                self.grid[*r][*c] = None;
            }
        }
    }

    fn forward_check(&self, slot_id: usize) -> bool {
        let slot = &self.slots[slot_id];
        for ix in &slot.intersections {
            if self.assignment[ix.other_slot_id].is_some() {
                continue;
            }
            let other_slot = &self.slots[ix.other_slot_id];
            let pattern = self.pattern_for_slot(other_slot);
            if !self
                .index
                .has_matching(&pattern, self.exclude_for_length(other_slot.length))
            {
                return false;
            }
        }
        true
    }

    fn select_mrv(&self) -> Option<usize> {
        let mut best_slot: Option<&Slot> = None;
        let mut best_count = usize::MAX;
        let mut best_degree = 0usize;
        let mut best_rarity = f64::NEG_INFINITY;

        for slot in self.slots {
            if self.assignment[slot.id].is_some() {
                continue;
            }
            let pattern = self.pattern_for_slot(slot);
            let count = self
                .index
                .count_matching(&pattern, self.exclude_for_length(slot.length));
            let degree = slot
                .intersections
                .iter()
                .filter(|ix| self.assignment[ix.other_slot_id].is_none())
                .count();
            let rarity = self
                .scarcity
                .map(|profile| profile.fixed_pattern_surprisal(slot.length, &pattern))
                .unwrap_or(0.0);
            let better = count < best_count
                || (count == best_count && degree > best_degree)
                || (count == best_count && degree == best_degree && rarity > best_rarity)
                || (count == best_count
                    && degree == best_degree
                    && (rarity - best_rarity).abs() < f64::EPSILON
                    && best_slot.is_some_and(|current| slot.length > current.length));
            if better {
                best_slot = Some(slot);
                best_count = count;
                best_degree = degree;
                best_rarity = rarity;
            }
        }
        best_slot.map(|slot| slot.id)
    }

    fn order_candidates<R: Rng + ?Sized>(&mut self, slot_id: usize, rng: &mut R) -> Vec<usize> {
        let slot = &self.slots[slot_id];
        let pattern = self.pattern_for_slot(slot);
        let mut candidates = self
            .index
            .matching_indices(&pattern, self.exclude_for_length(slot.length));
        if candidates.len() <= 1 {
            return candidates;
        }
        let Some(bucket) = self.index.bucket(slot.length) else {
            return Vec::new();
        };
        let mut scored: Vec<(usize, usize, f64, f64)> = Vec::with_capacity(candidates.len());
        for candidate_idx in candidates.drain(..) {
            if !self.assign_word(slot_id, candidate_idx) {
                continue;
            }
            let mut impact = 0usize;
            for ix in &slot.intersections {
                if self.assignment[ix.other_slot_id].is_some() {
                    continue;
                }
                let other_slot = &self.slots[ix.other_slot_id];
                let other_pattern = self.pattern_for_slot(other_slot);
                impact += self
                    .index
                    .count_matching(&other_pattern, self.exclude_for_length(other_slot.length));
            }
            let quality = bucket.words[candidate_idx].quality.definability_score;
            let rarity = self
                .scarcity
                .map(|profile| {
                    profile.open_position_word_surprisal(
                        slot.length,
                        &pattern,
                        &bucket.words[candidate_idx].chars,
                    )
                })
                .unwrap_or(0.0);
            self.unassign_word(slot_id, candidate_idx);
            scored.push((candidate_idx, impact, rarity, quality));
        }
        scored.shuffle(rng);
        scored.sort_by(|left, right| {
            right
                .1
                .cmp(&left.1)
                .then_with(|| {
                    right
                        .2
                        .partial_cmp(&left.2)
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .then_with(|| {
                    right
                        .3
                        .partial_cmp(&left.3)
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
        });
        scored.into_iter().map(|entry| entry.0).collect()
    }
}

fn solve_recursive<R: Rng + ?Sized>(
    state: &mut SolveState<'_>,
    stats: &mut SolveStats,
    max_nodes: usize,
    cancel: &AtomicBool,
    deadline: Option<Instant>,
    rng: &mut R,
) -> bool {
    if cancel.load(Ordering::Relaxed) {
        return false;
    }
    if deadline.is_some_and(|limit| Instant::now() >= limit) {
        stats.timed_out = true;
        return false;
    }
    if state.assignment.iter().all(Option::is_some) {
        return true;
    }
    let Some(slot_id) = state.select_mrv() else {
        return true;
    };
    let candidates = state.order_candidates(slot_id, rng);
    for candidate_idx in candidates {
        if cancel.load(Ordering::Relaxed) {
            return false;
        }
        if deadline.is_some_and(|limit| Instant::now() >= limit) {
            stats.timed_out = true;
            return false;
        }
        stats.nodes += 1;
        if stats.nodes > max_nodes {
            stats.node_limit_hit = true;
            return false;
        }
        if !state.assign_word(slot_id, candidate_idx) {
            continue;
        }
        if state.forward_check(slot_id)
            && solve_recursive(state, stats, max_nodes, cancel, deadline, rng)
        {
            return true;
        }
        state.unassign_word(slot_id, candidate_idx);
    }
    false
}

pub fn solve_grid<R: Rng + ?Sized>(
    template: &[Vec<bool>],
    slots: &[Slot],
    index: &WordIndex,
    scarcity: Option<&RuntimeSizeDictionaryProfile>,
    max_nodes: usize,
    allow_reuse: bool,
    cancel: &AtomicBool,
    deadline: Option<Instant>,
    rng: &mut R,
) -> (
    Option<(Vec<Option<usize>>, Vec<Vec<Option<char>>>)>,
    SolveStats,
) {
    let rows = template.len();
    let cols = template.first().map_or(0, |row| row.len());
    let mut state = SolveState {
        slots,
        index,
        scarcity,
        grid: (0..rows)
            .map(|r| {
                (0..cols)
                    .map(|c| if template[r][c] { None } else { Some('#') })
                    .collect()
            })
            .collect(),
        assignment: vec![None; slots.len()],
        used_by_length: HashMap::new(),
        cell_use_count: vec![vec![0; cols]; rows],
        allow_reuse,
    };
    let mut stats = SolveStats::default();
    if solve_recursive(&mut state, &mut stats, max_nodes, cancel, deadline, rng) {
        (Some((state.assignment, state.grid)), stats)
    } else {
        (None, stats)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dictionary_profile::{RuntimeDictionaryProfile, build_dictionary_profile};
    use crate::quality::score_words;
    use crate::slots::extract_slots;
    use crate::words::{RawWord, WordEntry, filter_word_records};
    use rand::{SeedableRng, rngs::StdRng};
    use std::sync::atomic::AtomicBool;

    fn words(rows: &[&str]) -> Vec<WordEntry> {
        filter_word_records(
            &rows
                .iter()
                .map(|word| RawWord {
                    normalized: (*word).to_string(),
                    original: word.to_lowercase(),
                    rarity_level: Some(5),
                    length: Some(word.len()),
                    word_type: None,
                    clue_support_score: None,
                    source: None,
                })
                .collect::<Vec<_>>(),
            8,
        )
        .0
    }

    #[test]
    fn bitset_index_matches_pattern() {
        let words = words(&["AB", "AC", "BC", "BA"]);
        let index = WordIndex::new(&words);
        assert_eq!(2, index.count_matching(&[Some('A'), None], None));
        assert_eq!(4, index.count_matching(&[None, None], None));
    }

    #[test]
    fn solves_small_grid() {
        let words = words(&["AB", "CD", "AC", "BD"]);
        let index = WordIndex::new(&words);
        let template = vec![vec![true, true], vec![true, true]];
        let slots = extract_slots(&template);
        let mut rng = StdRng::seed_from_u64(42);
        let cancel = AtomicBool::new(false);
        let solved = solve_grid(
            &template, &slots, &index, None, 1_000, false, &cancel, None, &mut rng,
        );
        assert!(solved.0.is_some());
        let (assignment, _) = solved.0.expect("solution");
        let solved_words: Vec<&WordEntry> = slots
            .iter()
            .map(|slot| {
                let idx = assignment[slot.id].expect("assigned");
                &index.bucket(slot.length).unwrap().words[idx]
            })
            .collect();
        let report = score_words(&solved_words, 2);
        assert_eq!(4, report.word_count);
    }

    #[test]
    fn positional_rarity_breaks_candidate_ties() {
        let raw_words = vec![
            RawWord {
                normalized: "ATA".to_string(),
                original: "ata".to_string(),
                rarity_level: Some(5),
                length: Some(3),
                word_type: None,
                clue_support_score: None,
                source: None,
            },
            RawWord {
                normalized: "AZA".to_string(),
                original: "aza".to_string(),
                rarity_level: Some(5),
                length: Some(3),
                word_type: None,
                clue_support_score: None,
                source: None,
            },
            RawWord {
                normalized: "MAM".to_string(),
                original: "mam".to_string(),
                rarity_level: Some(5),
                length: Some(3),
                word_type: None,
                clue_support_score: None,
                source: None,
            },
            RawWord {
                normalized: "NAN".to_string(),
                original: "nan".to_string(),
                rarity_level: Some(5),
                length: Some(3),
                word_type: None,
                clue_support_score: None,
                source: None,
            },
            RawWord {
                normalized: "RAR".to_string(),
                original: "rar".to_string(),
                rarity_level: Some(5),
                length: Some(3),
                word_type: None,
                clue_support_score: None,
                source: None,
            },
            RawWord {
                normalized: "SAS".to_string(),
                original: "sas".to_string(),
                rarity_level: Some(5),
                length: Some(3),
                word_type: None,
                clue_support_score: None,
                source: None,
            },
            RawWord {
                normalized: "LAL".to_string(),
                original: "lal".to_string(),
                rarity_level: Some(5),
                length: Some(3),
                word_type: None,
                clue_support_score: None,
                source: None,
            },
        ];
        let filtered = filter_word_records(&raw_words, 7).0;
        let index = WordIndex::new(&filtered);
        let slots = extract_slots(&[vec![true, true, true]]);
        let artifact = build_dictionary_profile(&raw_words, "words.json");
        let runtime = RuntimeDictionaryProfile::from_artifact(artifact);
        let scarcity = runtime.size(7).expect("size 7 profile");
        let mut state = SolveState {
            slots: &slots,
            index: &index,
            scarcity: Some(scarcity),
            grid: vec![vec![Some('A'), None, Some('A')]],
            assignment: vec![None; slots.len()],
            used_by_length: HashMap::new(),
            cell_use_count: vec![vec![0; 3]],
            allow_reuse: false,
        };
        let mut rng = StdRng::seed_from_u64(42);
        let ordered = state.order_candidates(0, &mut rng);
        let bucket = index.bucket(3).expect("bucket");
        let solved = ordered
            .into_iter()
            .map(|idx| bucket.words[idx].normalized.as_str())
            .collect::<Vec<_>>();
        assert_eq!("AZA", solved[0]);
    }
}
