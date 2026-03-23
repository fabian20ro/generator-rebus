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
}

#[derive(Clone, Debug)]
pub struct WordEntry {
    pub normalized: String,
    pub original: String,
    pub chars: Vec<char>,
    pub quality: WordQualityProfile,
}

impl WordEntry {
    pub fn len(&self) -> usize {
        self.chars.len()
    }
}

pub fn filter_word_records(raw_words: &[RawWord], max_length: usize) -> Vec<WordEntry> {
    raw_words
        .iter()
        .filter_map(|word| {
            let chars: Vec<char> = word.normalized.chars().collect();
            let length = chars.len();
            if length < 2 || length > max_length {
                return None;
            }
            if is_toxic_short_loanword(word) {
                return None;
            }
            let quality = assess_word_quality(word);
            if quality.definability_score < 1.5 {
                return None;
            }
            Some(WordEntry {
                normalized: word.normalized.clone(),
                original: if word.original.is_empty() {
                    word.normalized.to_lowercase()
                } else {
                    word.original.clone()
                },
                chars,
                quality,
            })
        })
        .collect()
}
