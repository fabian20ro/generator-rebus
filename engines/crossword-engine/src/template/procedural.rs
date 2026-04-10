use rand::{seq::SliceRandom, Rng};

use super::validate::{creates_single_letter, validate_template, black_spacing_ok};

pub fn generate_procedural_template<R: Rng + ?Sized>(
    size: usize,
    target_blacks: usize,
    max_attempts: usize,
    rng: &mut R,
) -> Option<Vec<Vec<bool>>> {
    for _ in 0..max_attempts {
        let mut grid = vec![vec![true; size]; size];
        let mut blacks_placed = 0usize;
        let mut cells: Vec<(usize, usize)> = (0..size)
            .flat_map(|r| (0..size).map(move |c| (r, c)))
            .collect();
        cells.shuffle(rng);
        for (r, c) in cells {
            if blacks_placed >= target_blacks {
                break;
            }
            grid[r][c] = false;
            if !black_spacing_ok(&grid, r, c, size) || creates_single_letter(&grid, r, c, size) {
                grid[r][c] = true;
                continue;
            }
            blacks_placed += 1;
        }
        if validate_template(&grid).is_ok() {
            return Some(grid);
        }
    }
    None
}
