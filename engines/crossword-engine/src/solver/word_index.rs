use std::collections::HashMap;

use crate::words::WordEntry;

use super::bitset::BitSet;

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
