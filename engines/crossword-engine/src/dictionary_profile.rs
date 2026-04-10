use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::words::{RawWord, WordEntry, filter_word_records};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PositionalLetterStat {
    pub count: usize,
    pub probability: f64,
    pub surprisal: f64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct SizeDictionaryProfile {
    pub size: usize,
    pub usable_word_count: usize,
    pub counts_by_length: BTreeMap<usize, usize>,
    pub medium_density: f64,
    pub long_density: f64,
    pub density_gap: f64,
    pub positional: BTreeMap<usize, Vec<BTreeMap<String, PositionalLetterStat>>>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct DictionaryProfileArtifact {
    pub source_words_path: String,
    pub sizes: BTreeMap<usize, SizeDictionaryProfile>,
}

#[derive(Clone, Debug, Default)]
pub struct RuntimeDictionaryProfile {
    sizes: HashMap<usize, RuntimeSizeDictionaryProfile>,
}

#[derive(Clone, Debug, Default)]
pub struct RuntimeSizeDictionaryProfile {
    pub size: usize,
    pub usable_word_count: usize,
    pub counts_by_length: HashMap<usize, usize>,
    pub medium_density: f64,
    pub long_density: f64,
    pub density_gap: f64,
    positional_surprisal: HashMap<usize, Vec<HashMap<char, f64>>>,
}

impl RuntimeDictionaryProfile {
    pub fn from_artifact(artifact: DictionaryProfileArtifact) -> Self {
        let sizes = artifact
            .sizes
            .into_iter()
            .map(|(size, profile)| (size, RuntimeSizeDictionaryProfile::from(profile)))
            .collect();
        Self { sizes }
    }

    pub fn size(&self, size: usize) -> Option<&RuntimeSizeDictionaryProfile> {
        self.sizes.get(&size)
    }
}

impl From<SizeDictionaryProfile> for RuntimeSizeDictionaryProfile {
    fn from(profile: SizeDictionaryProfile) -> Self {
        let positional_surprisal = profile
            .positional
            .iter()
            .map(|(length, positions)| {
                let converted = positions
                    .iter()
                    .map(|entries| {
                        entries
                            .iter()
                            .filter_map(|(key, stat)| {
                                key.chars().next().map(|ch| (ch, stat.surprisal))
                            })
                            .collect::<HashMap<char, f64>>()
                    })
                    .collect::<Vec<_>>();
                (*length, converted)
            })
            .collect();
        Self {
            size: profile.size,
            usable_word_count: profile.usable_word_count,
            counts_by_length: profile.counts_by_length.into_iter().collect(),
            medium_density: profile.medium_density,
            long_density: profile.long_density,
            density_gap: profile.density_gap,
            positional_surprisal,
        }
    }
}

impl RuntimeSizeDictionaryProfile {
    pub fn positional_surprisal(&self, length: usize, position: usize, ch: char) -> f64 {
        self.positional_surprisal
            .get(&length)
            .and_then(|positions| positions.get(position))
            .and_then(|entries| entries.get(&ch))
            .copied()
            .unwrap_or(0.0)
    }

    pub fn fixed_pattern_surprisal(&self, length: usize, pattern: &[Option<char>]) -> f64 {
        pattern
            .iter()
            .enumerate()
            .filter_map(|(position, value)| {
                value.map(|ch| self.positional_surprisal(length, position, ch))
            })
            .sum()
    }

    pub fn open_position_word_surprisal(
        &self,
        length: usize,
        pattern: &[Option<char>],
        chars: &[char],
    ) -> f64 {
        chars
            .iter()
            .enumerate()
            .filter(|(position, _)| pattern.get(*position).is_some_and(Option::is_none))
            .map(|(position, ch)| self.positional_surprisal(length, position, *ch))
            .sum()
    }
}

pub fn dictionary_profile_path(words_path: impl AsRef<Path>) -> PathBuf {
    let words_path = words_path.as_ref();
    let stem = words_path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("words");
    let file_name = format!("{stem}.profile.json");
    words_path.with_file_name(file_name)
}

pub fn build_dictionary_profile(
    raw_words: &[RawWord],
    source_words_path: impl Into<String>,
) -> DictionaryProfileArtifact {
    let mut sizes = BTreeMap::new();
    for size in 7..=15 {
        let (filtered_words, _) = filter_word_records(raw_words, size);
        sizes.insert(size, size_profile(size, &filtered_words));
    }
    DictionaryProfileArtifact {
        source_words_path: source_words_path.into(),
        sizes,
    }
}

pub fn write_dictionary_profile(
    words_path: impl AsRef<Path>,
    output_path: impl AsRef<Path>,
) -> Result<DictionaryProfileArtifact, DictionaryProfileIoError> {
    let words_path = words_path.as_ref();
    let raw_words: Vec<RawWord> = serde_json::from_str(
        &fs::read_to_string(words_path).map_err(DictionaryProfileIoError::ReadWords)?,
    )
    .map_err(DictionaryProfileIoError::ParseWords)?;
    let artifact = build_dictionary_profile(&raw_words, words_path.display().to_string());
    let serialized =
        serde_json::to_string(&artifact).map_err(DictionaryProfileIoError::SerializeProfile)?;
    fs::write(output_path, serialized).map_err(DictionaryProfileIoError::WriteProfile)?;
    Ok(artifact)
}

pub fn load_runtime_dictionary_profile(
    words_path: impl AsRef<Path>,
) -> Result<RuntimeDictionaryProfile, DictionaryProfileIoError> {
    let profile_path = dictionary_profile_path(words_path);
    let payload = fs::read_to_string(profile_path)
        .map_err(DictionaryProfileIoError::ReadDictionaryProfile)?;
    let artifact: DictionaryProfileArtifact =
        serde_json::from_str(&payload).map_err(DictionaryProfileIoError::ParseDictionaryProfile)?;
    Ok(RuntimeDictionaryProfile::from_artifact(artifact))
}

fn size_profile(size: usize, filtered_words: &[WordEntry]) -> SizeDictionaryProfile {
    let mut counts_by_length = BTreeMap::new();
    for word in filtered_words {
        *counts_by_length.entry(word.len()).or_insert(0) += 1;
    }

    let medium_density = average_log_bucket_density(&counts_by_length, 5..=size.min(8));
    let long_density =
        average_log_bucket_density(&counts_by_length, size.saturating_sub(2).max(5)..=size);
    let density_gap = (medium_density - long_density).max(0.0);

    let mut positional = BTreeMap::new();
    let mut words_by_length: HashMap<usize, Vec<&WordEntry>> = HashMap::new();
    for word in filtered_words {
        words_by_length.entry(word.len()).or_default().push(word);
    }
    for (length, bucket_words) in words_by_length {
        let total = bucket_words.len() as f64;
        let mut positions: Vec<BTreeMap<String, PositionalLetterStat>> =
            vec![BTreeMap::new(); length];
        for word in bucket_words {
            for (position, ch) in word.chars.iter().enumerate() {
                let key = ch.to_string();
                let entry = positions[position]
                    .entry(key)
                    .or_insert(PositionalLetterStat {
                        count: 0,
                        probability: 0.0,
                        surprisal: 0.0,
                    });
                entry.count += 1;
            }
        }
        for entries in &mut positions {
            for stat in entries.values_mut() {
                stat.probability = stat.count as f64 / total.max(1.0);
                stat.surprisal = -stat.probability.max(1e-9).ln();
            }
        }
        positional.insert(length, positions);
    }

    SizeDictionaryProfile {
        size,
        usable_word_count: filtered_words.len(),
        counts_by_length,
        medium_density,
        long_density,
        density_gap,
        positional,
    }
}

fn average_log_bucket_density(
    counts_by_length: &BTreeMap<usize, usize>,
    lengths: std::ops::RangeInclusive<usize>,
) -> f64 {
    let mut total = 0.0f64;
    let mut seen = 0usize;
    for length in lengths {
        total += (counts_by_length.get(&length).copied().unwrap_or(0) as f64 + 1.0).ln();
        seen += 1;
    }
    if seen == 0 { 0.0 } else { total / seen as f64 }
}

#[derive(Debug)]
pub enum DictionaryProfileIoError {
    ReadWords(std::io::Error),
    ParseWords(serde_json::Error),
    WriteProfile(std::io::Error),
    SerializeProfile(serde_json::Error),
    ReadDictionaryProfile(std::io::Error),
    ParseDictionaryProfile(serde_json::Error),
}

impl std::fmt::Display for DictionaryProfileIoError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ReadWords(err) => write!(f, "read words: {err}"),
            Self::ParseWords(err) => write!(f, "parse words: {err}"),
            Self::WriteProfile(err) => write!(f, "write dictionary profile: {err}"),
            Self::SerializeProfile(err) => write!(f, "serialize dictionary profile: {err}"),
            Self::ReadDictionaryProfile(err) => write!(f, "read dictionary profile: {err}"),
            Self::ParseDictionaryProfile(err) => write!(f, "parse dictionary profile: {err}"),
        }
    }
}

impl std::error::Error for DictionaryProfileIoError {}

#[cfg(test)]
mod tests {
    use super::*;

    fn raw_words(rows: &[&str]) -> Vec<RawWord> {
        rows.iter()
            .map(|word| RawWord {
                normalized: (*word).to_string(),
                original: (*word).to_string(),
                rarity_level: Some(1),
                length: Some(word.chars().count()),
                word_type: None,
            })
            .collect()
    }

    #[test]
    fn profile_path_uses_sidecar_name() {
        let path = dictionary_profile_path("/tmp/build/words.json");
        assert_eq!(PathBuf::from("/tmp/build/words.profile.json"), path);
    }

    #[test]
    fn build_profile_tracks_positional_counts_and_surprisal() {
        let artifact =
            build_dictionary_profile(&raw_words(&["TATAR", "TOTEM", "ZIDAR"]), "words.json");
        let size7 = artifact.sizes.get(&7).expect("size 7 profile");
        let length5 = size7.positional.get(&5).expect("length 5 positional");
        let first_position = &length5[0];
        assert_eq!(2, first_position["T"].count);
        assert_eq!(1, first_position["Z"].count);
        assert!(first_position["Z"].surprisal > first_position["T"].surprisal);
    }
}
