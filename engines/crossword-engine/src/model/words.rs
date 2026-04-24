use std::collections::HashMap;

use serde::Deserialize;

use crate::quality::{WordQualityProfile, assess_word_quality, is_toxic_short_loanword};

#[derive(Clone, Deserialize, Debug)]
pub struct RawWord {
    pub normalized: String,
    #[serde(default)]
    pub original: String,
    #[serde(default)]
    pub rarity_level: Option<i32>,
    #[serde(default)]
    pub length: Option<usize>,
    #[serde(default)]
    pub word_type: Option<String>,
    #[serde(default)]
    pub clue_support_score: Option<f64>,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Clone, Debug)]
pub struct WordEntry {
    pub normalized: String,
    pub original: String,
    pub chars: Vec<char>,
    pub min_rarity: i32,
    pub quality: WordQualityProfile,
    pub clue_support_score: f64,
    pub source: String,
}

impl WordEntry {
    pub fn len(&self) -> usize {
        self.chars.len()
    }
}

#[derive(Clone, Debug, Default)]
pub struct DictionaryLoadStats {
    pub total_rows: usize,
    pub unique_words: usize,
    pub duplicate_rows: usize,
    pub skipped_rows: usize,
}

pub fn filter_word_records(
    raw_words: &[RawWord],
    max_length: usize,
) -> (Vec<WordEntry>, DictionaryLoadStats) {
    let mut grouped: HashMap<String, Vec<&RawWord>> = HashMap::new();
    let mut stats = DictionaryLoadStats {
        total_rows: raw_words.len(),
        ..DictionaryLoadStats::default()
    };

    for word in raw_words {
        let normalized = word.normalized.trim();
        if normalized.is_empty() {
            stats.skipped_rows += 1;
            continue;
        }
        let chars: Vec<char> = normalized.chars().collect();
        let length = chars.len();
        if matches!(word.length, Some(expected) if expected != length) {
            stats.skipped_rows += 1;
            continue;
        }
        if length < 2 || length > max_length {
            stats.skipped_rows += 1;
            continue;
        }
        grouped
            .entry(normalized.to_string())
            .or_default()
            .push(word);
    }

    let grouped_count = grouped.len();
    stats.duplicate_rows = raw_words
        .len()
        .saturating_sub(grouped_count + stats.skipped_rows);

    let mut entries = Vec::new();
    for (normalized, variants) in grouped {
        if is_toxic_short_loanword(&normalized) {
            stats.skipped_rows += variants.len();
            continue;
        }
        let min_rarity = variants
            .iter()
            .filter_map(|variant| variant.rarity_level)
            .min()
            .unwrap_or(3);
        let clue_support_score = variants
            .iter()
            .filter_map(|variant| variant.clue_support_score)
            .fold(0.0_f64, f64::max)
            .clamp(0.0, 8.0);
        let original = variants
            .iter()
            .find_map(|variant| {
                let original = variant.original.trim();
                if original.is_empty() {
                    None
                } else {
                    Some(original.to_string())
                }
            })
            .unwrap_or_else(|| normalized.to_lowercase());
        let source = variants
            .iter()
            .filter_map(|variant| variant.source.as_ref())
            .find(|source| !source.trim().is_empty())
            .cloned()
            .unwrap_or_default();
        let quality = assess_word_quality(&normalized, min_rarity, clue_support_score);
        if quality.definability_score < 1.0 {
            stats.skipped_rows += variants.len();
            continue;
        }
        let chars: Vec<char> = normalized.chars().collect();
        entries.push(WordEntry {
            normalized,
            original,
            chars,
            min_rarity,
            quality,
            clue_support_score,
            source,
        });
    }

    entries.sort_by(|left, right| left.normalized.cmp(&right.normalized));
    stats.unique_words = entries.len();
    (entries, stats)
}
