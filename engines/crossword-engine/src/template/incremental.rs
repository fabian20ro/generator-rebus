use rand::{Rng, seq::SliceRandom};

use super::validate::{black_spacing_ok, is_connected, placement_creates_invalid_slots};

pub fn generate_incremental_template<R, F>(
    size: usize,
    solver_fn: &F,
    max_blacks: usize,
    min_solver_step: usize,
    rng: &mut R,
) -> Option<Vec<Vec<bool>>>
where
    R: Rng + ?Sized,
    F: Fn(&[Vec<bool>]) -> bool,
{
    let mut grid = vec![vec![true; size]; size];
    if min_solver_step == 0 && solver_fn(&grid) {
        return Some(grid);
    }
    let mut all_cells: Vec<(usize, usize)> = (0..size)
        .flat_map(|r| (0..size).map(move |c| (r, c)))
        .collect();
    for step in 1..=max_blacks {
        all_cells.shuffle(rng);
        let mut placed = false;
        for &(r, c) in &all_cells {
            if !grid[r][c] {
                continue;
            }
            if !black_spacing_ok(&grid, r, c, size) {
                continue;
            }
            grid[r][c] = false;
            if placement_creates_invalid_slots(&grid, r, c, size) || !is_connected(&grid) {
                grid[r][c] = true;
                continue;
            }
            placed = true;
            break;
        }
        if !placed {
            return None;
        }
        if step >= min_solver_step && solver_fn(&grid) {
            return Some(grid);
        }
    }
    None
}
