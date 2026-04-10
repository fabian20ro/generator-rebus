use std::collections::{HashSet, VecDeque};

pub fn validate_template(grid: &[Vec<bool>]) -> Result<(), String> {
    let rows = grid.len();
    let cols = grid.first().map_or(0, |row| row.len());

    for (r, row) in grid.iter().enumerate() {
        let black_cols: Vec<usize> = row
            .iter()
            .enumerate()
            .filter_map(|(c, cell)| if !cell { Some(c) } else { None })
            .collect();
        for pair in black_cols.windows(2) {
            if pair[1] - pair[0] < 3 {
                return Err(format!("Blacks too close on row {r}"));
            }
        }
    }

    for c in 0..cols {
        let black_rows: Vec<usize> = (0..rows).filter(|r| !grid[*r][c]).collect();
        for pair in black_rows.windows(2) {
            if pair[1] - pair[0] < 3 {
                return Err(format!("Blacks too close on col {c}"));
            }
        }
    }

    for (r, row) in grid.iter().enumerate() {
        let mut run = 0usize;
        for c in 0..=cols {
            if c < cols && row[c] {
                run += 1;
            } else {
                if run == 1 {
                    return Err(format!("Single-letter horizontal slot at row {r}"));
                }
                run = 0;
            }
        }
    }

    for c in 0..cols {
        let mut run = 0usize;
        for r in 0..=rows {
            if r < rows && grid[r][c] {
                run += 1;
            } else {
                if run == 1 {
                    return Err(format!("Single-letter vertical slot at col {c}"));
                }
                run = 0;
            }
        }
    }

    if !is_connected(grid) {
        return Err("Not connected".to_string());
    }

    Ok(())
}

pub fn is_connected(grid: &[Vec<bool>]) -> bool {
    let rows = grid.len();
    let cols = grid.first().map_or(0, |row| row.len());
    let mut start = None;
    let mut letter_count = 0usize;
    for r in 0..rows {
        for c in 0..cols {
            if grid[r][c] {
                letter_count += 1;
                if start.is_none() {
                    start = Some((r, c));
                }
            }
        }
    }
    let Some(start_cell) = start else {
        return false;
    };
    let mut visited: HashSet<(usize, usize)> = HashSet::new();
    let mut queue = VecDeque::from([start_cell]);
    visited.insert(start_cell);
    while let Some((r, c)) = queue.pop_front() {
        for (dr, dc) in [(-1isize, 0isize), (1, 0), (0, -1), (0, 1)] {
            let nr = r as isize + dr;
            let nc = c as isize + dc;
            if nr < 0 || nc < 0 || nr >= rows as isize || nc >= cols as isize {
                continue;
            }
            let next = (nr as usize, nc as usize);
            if grid[next.0][next.1] && visited.insert(next) {
                queue.push_back(next);
            }
        }
    }
    visited.len() == letter_count
}

pub fn black_spacing_ok(grid: &[Vec<bool>], r: usize, c: usize, size: usize) -> bool {
    for d in [-2isize, -1, 1, 2] {
        let nc = c as isize + d;
        if (0..size as isize).contains(&nc) && !grid[r][nc as usize] {
            return false;
        }
        let nr = r as isize + d;
        if (0..size as isize).contains(&nr) && !grid[nr as usize][c] {
            return false;
        }
    }
    true
}

pub fn creates_single_letter(grid: &[Vec<bool>], br: usize, bc: usize, size: usize) -> bool {
    let check_row = |row: usize, grid: &[Vec<bool>]| -> bool {
        let mut run = 0usize;
        for c in 0..=size {
            if c < size && grid[row][c] {
                run += 1;
            } else {
                if run == 1 {
                    return true;
                }
                run = 0;
            }
        }
        false
    };
    let check_col = |col: usize, grid: &[Vec<bool>]| -> bool {
        let mut run = 0usize;
        for r in 0..=size {
            if r < size && grid[r][col] {
                run += 1;
            } else {
                if run == 1 {
                    return true;
                }
                run = 0;
            }
        }
        false
    };

    if check_row(br, grid) || check_col(bc, grid) {
        return true;
    }
    for (dr, dc) in [(-1isize, 0isize), (1, 0), (0, -1), (0, 1)] {
        let nr = br as isize + dr;
        let nc = bc as isize + dc;
        if nr < 0 || nc < 0 || nr >= size as isize || nc >= size as isize {
            continue;
        }
        let nr = nr as usize;
        let nc = nc as usize;
        if grid[nr][nc] && ((dc == 0 && check_row(nr, grid)) || (dr == 0 && check_col(nc, grid))) {
            return true;
        }
    }
    false
}
