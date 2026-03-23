use serde::Serialize;

use crate::words::{RawWord, WordEntry};

const FOREIGN_SHORTLIST_BLOCKLIST: &[&str] =
    &["AIR", "BIG", "CAT", "DIG", "DOG", "GET", "LAW", "TEN"];
const UNCOMMON_LETTERS: &[char] = &['Q', 'W', 'X', 'Y', 'K'];

#[derive(Clone, Debug)]
pub struct WordQualityProfile {
    pub short_fragility: i32,
    pub ambiguity_risk: i32,
    pub family_leak_risk: i32,
    pub foreign_risk: i32,
    pub abbreviation_like: bool,
    pub definability_score: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct QualityReport {
    pub score: f64,
    pub word_count: usize,
    pub average_length: f64,
    pub average_rarity: f64,
    pub two_letter_words: usize,
    pub three_letter_words: usize,
    pub high_rarity_words: usize,
    pub uncommon_letter_words: usize,
    pub friendly_words: usize,
    pub max_rarity: i32,
    pub average_definability: f64,
}

pub fn is_toxic_short_loanword(word: &RawWord) -> bool {
    if !FOREIGN_SHORTLIST_BLOCKLIST.contains(&word.normalized.as_str()) {
        return false;
    }
    word.original.is_ascii()
        && word
            .original
            .chars()
            .all(|ch| !ch.is_ascii_alphabetic() || ch.is_ascii_lowercase())
}

pub fn assess_word_quality(word: &RawWord) -> WordQualityProfile {
    let length = word.normalized.chars().count();
    let original = if word.original.is_empty() {
        word.normalized.to_lowercase()
    } else {
        word.original.clone()
    };
    let short_fragility = if length <= 2 {
        4
    } else if length == 3 {
        3
    } else if length == 4 {
        1
    } else {
        0
    };
    let ambiguity_risk = if length <= 3 {
        3
    } else if length == 4 {
        2
    } else if length <= 6 {
        1
    } else {
        0
    };
    let family_leak_risk = if length >= 6
        && ["ARE", "IRE", "ATE", "ISM"]
            .iter()
            .any(|suffix| word.normalized.ends_with(suffix))
    {
        2
    } else {
        0
    };
    let foreign_risk = if is_toxic_short_loanword(word) {
        3
    } else if original.is_ascii()
        && original
            .chars()
            .all(|ch| !ch.is_ascii_alphabetic() || ch.is_ascii_lowercase())
    {
        1
    } else {
        0
    };
    let abbreviation_like = length <= 3
        && original.is_ascii()
        && original
            .chars()
            .all(|ch| !ch.is_ascii_alphabetic() || ch.is_ascii_lowercase());
    let definability_score = (10.0
        - short_fragility as f64
        - ambiguity_risk as f64
        - family_leak_risk as f64
        - foreign_risk as f64)
        .max(0.0);
    WordQualityProfile {
        short_fragility,
        ambiguity_risk,
        family_leak_risk,
        foreign_risk,
        abbreviation_like,
        definability_score,
    }
}

pub fn score_words(words: &[&WordEntry], size: usize) -> QualityReport {
    let lengths: Vec<usize> = words.iter().map(|word| word.len()).collect();
    let avg_length = if lengths.is_empty() {
        0.0
    } else {
        lengths.iter().sum::<usize>() as f64 / lengths.len() as f64
    };
    let two_letter = lengths.iter().filter(|length| **length == 2).count();
    let three_letter = lengths.iter().filter(|length| **length == 3).count();
    let uncommon = words
        .iter()
        .filter(|word| word.chars.iter().any(|ch| UNCOMMON_LETTERS.contains(ch)))
        .count();
    let friendly = words
        .iter()
        .filter(|word| {
            let len = word.len();
            (4..=8).contains(&len) && word.quality.definability_score >= 5.0
        })
        .count();
    let avg_definability = if words.is_empty() {
        0.0
    } else {
        words
            .iter()
            .map(|word| word.quality.definability_score)
            .sum::<f64>()
            / words.len() as f64
    };

    let (two_letter_penalty, three_letter_penalty, extra_two_penalty) = match size {
        7 => (34.0, 12.0, 18.0),
        10 => (22.0, 8.0, 12.0),
        12 => (18.0, 6.0, 10.0),
        _ => (14.0, 5.0, 8.0),
    };
    let extra_two_limit = match size {
        7 => 2,
        10 => 5,
        12 => 8,
        _ => 9,
    };

    let mut score = 1000.0;
    score += avg_length * 14.0;
    score += friendly as f64 * 4.0;
    score += avg_definability * 9.0;
    score -= two_letter as f64 * two_letter_penalty;
    score -= three_letter as f64 * three_letter_penalty;
    score -= uncommon as f64 * 10.0;
    score -= usize::saturating_sub(two_letter, extra_two_limit) as f64 * extra_two_penalty;

    QualityReport {
        score,
        word_count: words.len(),
        average_length: avg_length,
        average_rarity: 0.0,
        two_letter_words: two_letter,
        three_letter_words: three_letter,
        high_rarity_words: 0,
        uncommon_letter_words: uncommon,
        friendly_words: friendly,
        max_rarity: 0,
        average_definability: avg_definability,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::words::RawWord;

    #[test]
    fn quality_ignores_rarity() {
        let low = RawWord {
            normalized: "AER".to_string(),
            original: "aer".to_string(),
            rarity_level: Some(1),
            length: Some(3),
            word_type: None,
        };
        let high = RawWord {
            normalized: "AER".to_string(),
            original: "aer".to_string(),
            rarity_level: Some(5),
            length: Some(3),
            word_type: None,
        };
        let low_profile = assess_word_quality(&low);
        let high_profile = assess_word_quality(&high);
        assert_eq!(
            low_profile.definability_score,
            high_profile.definability_score
        );
    }
}
