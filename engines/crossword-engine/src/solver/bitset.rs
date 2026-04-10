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
