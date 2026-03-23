use std::collections::HashMap;

use rand::Rng;
use rand::seq::SliceRandom;

use crate::slots::Slot;
use crate::words::WordEntry;

#[derive(Clone, Debug)]
pub struct BitSet {
    words: Vec<u64>,
}

impl BitSet {
    pub fn empty(bits: usize) -> Self {
        Self {
            words: vec![0; bits.div_ceil(64)],
        }
    }

    pub fn with_all(bits: usize) -> Self {
        let mut set = Self::empty(bits);
        for idx in 0..bits {
            set.set(idx);
        }
        set
    }

    pub fn set(&mut self, idx: usize) {
        let word = idx / 64;
        let bit = idx % 64;
        self.words[word] |= 1u64 << bit;
    }

    pub fn clear(&mut self, idx: usize) {
        let word = idx / 64;
        let bit = idx % 64;
        self.words[word] &= !(1u64 << bit);
    }

    pub fn and_inplace(&mut self, other: &BitSet) {
        for (left, right) in self.words.iter_mut().zip(&other.words) {
            *left &= *right;
        }
    }

    pub fn and_not_inplace(&mut self, other: &BitSet) {
        for (left, right) in self.words.iter_mut().zip(&other.words) {
            *left &= !*right;
        }
    }

    pub fn count_ones(&self) -> usize {
        self.words
            .iter()
            .map(|word| word.count_ones() as usize)
            .sum()
    }

    pub fn is_empty(&self) -> bool {
        self.words.iter().all(|word| *word == 0)
    }

    pub fn any(&self) -> bool {
        !self.is_empty()
    }

    pub fn iter_indices(&self) -> Vec<usize> {
        let mut result = Vec::new();
        for (word_index, word) in self.words.iter().enumerate() {
            let mut remaining = *word;
            while remaining != 0 {
                let bit = remaining.trailing_zeros() as usize;
                result.push(word_index * 64 + bit);
                remaining &= remaining - 1;
            }
        }
        result
    }
}

#[derive(Clone, Debug)]
pub struct Bucket {
    pub words: Vec<WordEntry>,
    positional: Vec<HashMap<char, BitSet>>,
    all_bits: BitSet,
}

#[derive(Clone, Debug)]
pub struct WordIndex {
    buckets: HashMap<usize, Bucket>,
}

impl WordIndex {
    pub fn new(words: &[WordEntry]) -> Self {
        let mut grouped: HashMap<usize, Vec<WordEntry>> = HashMap::new();
        for word in words {
            grouped.entry(word.len()).or_default().push(word.clone());
        }
        let buckets = grouped
            .into_iter()
            .map(|(length, bucket_words)| {
                let mut positional: Vec<HashMap<char, BitSet>> = vec![HashMap::new(); length];
                for (idx, word) in bucket_words.iter().enumerate() {
                    for (pos, ch) in word.chars.iter().enumerate() {
                        positional[pos]
                            .entry(*ch)
                            .or_insert_with(|| BitSet::empty(bucket_words.len()))
                            .set(idx);
                    }
                }
                let all_bits = BitSet::with_all(bucket_words.len());
                (
                    length,
                    Bucket {
                        words: bucket_words,
                        positional,
                        all_bits,
                    },
                )
            })
            .collect();
        Self { buckets }
    }

    pub fn bucket(&self, length: usize) -> Option<&Bucket> {
        self.buckets.get(&length)
    }

    fn matching_bitset(&self, pattern: &[Option<char>]) -> Option<BitSet> {
        let bucket = self.buckets.get(&pattern.len())?;
        let mut result: Option<BitSet> = None;
        for (pos, ch) in pattern.iter().enumerate() {
            let Some(ch) = ch else {
                continue;
            };
            let matching = bucket.positional[pos].get(ch)?;
            if let Some(current) = result.as_mut() {
                current.and_inplace(matching);
                if current.is_empty() {
                    return result;
                }
            } else {
                result = Some(matching.clone());
            }
        }
        Some(result.unwrap_or_else(|| bucket.all_bits.clone()))
    }

    pub fn count_matching(&self, pattern: &[Option<char>], exclude: Option<&BitSet>) -> usize {
        let Some(mut mask) = self.matching_bitset(pattern) else {
            return 0;
        };
        if let Some(exclude) = exclude {
            mask.and_not_inplace(exclude);
        }
        mask.count_ones()
    }

    pub fn has_matching(&self, pattern: &[Option<char>], exclude: Option<&BitSet>) -> bool {
        self.count_matching(pattern, exclude) > 0
    }

    pub fn matching_indices(
        &self,
        pattern: &[Option<char>],
        exclude: Option<&BitSet>,
    ) -> Vec<usize> {
        let Some(mut mask) = self.matching_bitset(pattern) else {
            return Vec::new();
        };
        if let Some(exclude) = exclude {
            mask.and_not_inplace(exclude);
        }
        mask.iter_indices()
    }
}

#[derive(Clone, Debug, Default)]
pub struct SolveStats {
    pub nodes: usize,
}

struct SolveState<'a> {
    slots: &'a [Slot],
    index: &'a WordIndex,
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

    fn assign_word(&mut self, slot_id: usize, word_idx: usize) {
        let slot = &self.slots[slot_id];
        let bucket = self.index.bucket(slot.length).expect("bucket");
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
            let better = count < best_count
                || (count == best_count && degree > best_degree)
                || (count == best_count
                    && degree == best_degree
                    && best_slot.is_some_and(|current| slot.length > current.length));
            if better {
                best_slot = Some(slot);
                best_count = count;
                best_degree = degree;
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
        let mut scored: Vec<(usize, usize, f64)> = Vec::with_capacity(candidates.len());
        for candidate_idx in candidates.drain(..) {
            self.assign_word(slot_id, candidate_idx);
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
            let bucket = self.index.bucket(slot.length).expect("bucket");
            let quality = bucket.words[candidate_idx].quality.definability_score;
            self.unassign_word(slot_id, candidate_idx);
            scored.push((candidate_idx, impact, quality));
        }
        scored.shuffle(rng);
        scored.sort_by(|left, right| {
            right.1.cmp(&left.1).then_with(|| {
                right
                    .2
                    .partial_cmp(&left.2)
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
    rng: &mut R,
) -> bool {
    if state.assignment.iter().all(Option::is_some) {
        return true;
    }
    let Some(slot_id) = state.select_mrv() else {
        return true;
    };
    let candidates = state.order_candidates(slot_id, rng);
    for candidate_idx in candidates {
        stats.nodes += 1;
        if stats.nodes > max_nodes {
            return false;
        }
        state.assign_word(slot_id, candidate_idx);
        if state.forward_check(slot_id) && solve_recursive(state, stats, max_nodes, rng) {
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
    max_nodes: usize,
    allow_reuse: bool,
    rng: &mut R,
) -> Option<(Vec<Option<usize>>, Vec<Vec<Option<char>>>, SolveStats)> {
    let rows = template.len();
    let cols = template.first().map_or(0, |row| row.len());
    let mut state = SolveState {
        slots,
        index,
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
    if solve_recursive(&mut state, &mut stats, max_nodes, rng) {
        Some((state.assignment, state.grid, stats))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::quality::score_words;
    use crate::slots::extract_slots;
    use crate::words::{RawWord, filter_word_records};
    use rand::{SeedableRng, rngs::StdRng};

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
                })
                .collect::<Vec<_>>(),
            8,
        )
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
        let solved = solve_grid(&template, &slots, &index, 1_000, false, &mut rng);
        assert!(solved.is_some());
        let (assignment, _, _) = solved.expect("solution");
        let solved_words: Vec<&WordEntry> = slots
            .iter()
            .map(|slot| {
                let idx = assignment[slot.id].expect("assigned");
                &index.bucket(slot.length).expect("bucket").words[idx]
            })
            .collect();
        let report = score_words(&solved_words, 2);
        assert_eq!(4, report.word_count);
    }
}
