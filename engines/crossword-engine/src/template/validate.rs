use std::collections::{HashSet, VecDeque};

fn is_edge_single(start: usize, end: usize, limit: usize) -> bool {
    start == end && (start == 0 || end + 1 == limit)
}

fn horizontal_run_len(grid: &[Vec<bool>], row: usize, col: usize, size: usize) -> usize {
    if !grid[row][col] {
        return 0;
    }
    let mut start = col;
    while start > 0 && grid[row][start - 1] {
        start -= 1;
    }
    let mut end = col;
    while end + 1 < size && grid[row][end + 1] {
        end += 1;
    }
    end - start + 1
}

fn vertical_run_len(grid: &[Vec<bool>], row: usize, col: usize, size: usize) -> usize {
    if !grid[row][col] {
        return 0;
    }
    let mut start = row;
    while start > 0 && grid[start - 1][col] {
        start -= 1;
    }
    let mut end = row;
    while end + 1 < size && grid[end + 1][col] {
        end += 1;
    }
    end - start + 1
}

fn row_has_invalid_singletons(grid: &[Vec<bool>], row: usize, size: usize) -> bool {
    let mut run = 0usize;
    let mut run_start = 0usize;
    for c in 0..=size {
        if c < size && grid[row][c] {
            if run == 0 {
                run_start = c;
            }
            run += 1;
        } else {
            if run == 1 {
                let run_end = c.saturating_sub(1);
                if !is_edge_single(run_start, run_end, size)
                    || vertical_run_len(grid, row, run_start, size) < 2
                {
                    return true;
                }
            }
            run = 0;
        }
    }
    false
}

fn col_has_invalid_singletons(grid: &[Vec<bool>], col: usize, size: usize) -> bool {
    let mut run = 0usize;
    let mut run_start = 0usize;
    for r in 0..=size {
        if r < size && grid[r][col] {
            if run == 0 {
                run_start = r;
            }
            run += 1;
        } else {
            if run == 1 {
                let run_end = r.saturating_sub(1);
                if !is_edge_single(run_start, run_end, size)
                    || horizontal_run_len(grid, run_start, col, size) < 2
                {
                    return true;
                }
            }
            run = 0;
        }
    }
    false
}

fn line_has_uncovered_white(grid: &[Vec<bool>], row: usize, col: usize, size: usize) -> bool {
    for c in 0..size {
        if grid[row][c]
            && horizontal_run_len(grid, row, c, size) < 2
            && vertical_run_len(grid, row, c, size) < 2
        {
            return true;
        }
    }
    for r in 0..size {
        if grid[r][col]
            && horizontal_run_len(grid, r, col, size) < 2
            && vertical_run_len(grid, r, col, size) < 2
        {
            return true;
        }
    }
    false
}

pub fn validate_template(grid: &[Vec<bool>]) -> Result<(), String> {
    let rows = grid.len();
    let cols = grid.first().map_or(0, |row| row.len());
    let mut horizontal_cover = vec![vec![false; cols]; rows];
    let mut vertical_cover = vec![vec![false; cols]; rows];

    for (r, row) in grid.iter().enumerate() {
        let black_cols: Vec<usize> = row
            .iter()
            .enumerate()
            .filter_map(|(c, cell)| if !cell { Some(c) } else { None })
            .collect();
        for pair in black_cols.windows(2) {
            if pair[1] - pair[0] < 2 {
                return Err(format!("Blacks too close on row {r}"));
            }
        }
    }

    for c in 0..cols {
        let black_rows: Vec<usize> = (0..rows).filter(|r| !grid[*r][c]).collect();
        for pair in black_rows.windows(2) {
            if pair[1] - pair[0] < 2 {
                return Err(format!("Blacks too close on col {c}"));
            }
        }
    }

    for (r, row) in grid.iter().enumerate() {
        let mut run = 0usize;
        let mut run_start = 0usize;
        for c in 0..=cols {
            if c < cols && row[c] {
                if run == 0 {
                    run_start = c;
                }
                run += 1;
            } else {
                if run == 1 {
                    let run_end = c.saturating_sub(1);
                    if !is_edge_single(run_start, run_end, cols) {
                        return Err(format!("Single-letter horizontal slot at row {r}"));
                    }
                } else if run >= 2 {
                    for cc in run_start..c {
                        horizontal_cover[r][cc] = true;
                    }
                }
                run = 0;
            }
        }
    }

    for c in 0..cols {
        let mut run = 0usize;
        let mut run_start = 0usize;
        for r in 0..=rows {
            if r < rows && grid[r][c] {
                if run == 0 {
                    run_start = r;
                }
                run += 1;
            } else {
                if run == 1 {
                    let run_end = r.saturating_sub(1);
                    if !is_edge_single(run_start, run_end, rows) {
                        return Err(format!("Single-letter vertical slot at col {c}"));
                    }
                } else if run >= 2 {
                    for rr in run_start..r {
                        vertical_cover[rr][c] = true;
                    }
                }
                run = 0;
            }
        }
    }

    for r in 0..rows {
        for c in 0..cols {
            if grid[r][c] && !horizontal_cover[r][c] && !vertical_cover[r][c] {
                return Err(format!("Uncovered white cell at row {r} col {c}"));
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
    for d in [-1isize, 1] {
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

pub fn placement_creates_invalid_slots(
    grid: &[Vec<bool>],
    br: usize,
    bc: usize,
    size: usize,
) -> bool {
    row_has_invalid_singletons(grid, br, size)
        || col_has_invalid_singletons(grid, bc, size)
        || line_has_uncovered_white(grid, br, bc, size)
}

pub fn count_edge_singletons(grid: &[Vec<bool>]) -> usize {
    let rows = grid.len();
    let cols = grid.first().map_or(0, |row| row.len());
    let mut count = 0usize;

    for row in grid.iter() {
        let mut run = 0usize;
        let mut run_start = 0usize;
        for c in 0..=cols {
            if c < cols && row[c] {
                if run == 0 {
                    run_start = c;
                }
                run += 1;
            } else {
                if run == 1 && is_edge_single(run_start, c.saturating_sub(1), cols) {
                    count += 1;
                }
                run = 0;
            }
        }
    }

    for c in 0..cols {
        let mut run = 0usize;
        let mut run_start = 0usize;
        for r in 0..=rows {
            if r < rows && grid[r][c] {
                if run == 0 {
                    run_start = r;
                }
                run += 1;
            } else {
                if run == 1 && is_edge_single(run_start, r.saturating_sub(1), rows) {
                    count += 1;
                }
                run = 0;
            }
        }
    }

    count
}

pub fn template_rejection_bucket(message: &str) -> &'static str {
    let lowered = message.to_ascii_lowercase();
    if lowered.contains("blacks too close") {
        "spacing"
    } else if lowered.contains("single-letter") {
        "singleton_interior"
    } else if lowered.contains("not connected") {
        "disconnected"
    } else if lowered.contains("uncovered white cell") {
        "uncovered_white"
    } else {
        "other"
    }
}

pub fn creates_single_letter(grid: &[Vec<bool>], br: usize, bc: usize, size: usize) -> bool {
    placement_creates_invalid_slots(grid, br, bc, size)
}
