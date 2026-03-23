use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct Intersection {
    pub other_slot_id: usize,
    pub this_position: usize,
    pub other_position: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct Slot {
    pub id: usize,
    pub direction: char,
    pub start_row: usize,
    pub start_col: usize,
    pub length: usize,
    pub cells: Vec<(usize, usize)>,
    pub intersections: Vec<Intersection>,
}

pub fn extract_slots(grid: &[Vec<bool>]) -> Vec<Slot> {
    let rows = grid.len();
    let cols = grid.first().map_or(0, |row| row.len());
    let mut slots = Vec::new();
    let mut slot_id = 0usize;
    let mut cell_to_slots: std::collections::HashMap<(usize, usize), Vec<(usize, usize)>> =
        std::collections::HashMap::new();

    for (r, row) in grid.iter().enumerate() {
        let mut run_start: Option<usize> = None;
        let mut run_cells: Vec<(usize, usize)> = Vec::new();
        for c in 0..=cols {
            if c < cols && row[c] {
                if run_start.is_none() {
                    run_start = Some(c);
                    run_cells.clear();
                }
                run_cells.push((r, c));
            } else {
                if let Some(start_col) = run_start {
                    if run_cells.len() >= 2 {
                        for (pos, cell) in run_cells.iter().enumerate() {
                            cell_to_slots.entry(*cell).or_default().push((slot_id, pos));
                        }
                        slots.push(Slot {
                            id: slot_id,
                            direction: 'H',
                            start_row: r,
                            start_col,
                            length: run_cells.len(),
                            cells: run_cells.clone(),
                            intersections: Vec::new(),
                        });
                        slot_id += 1;
                    }
                }
                run_start = None;
                run_cells.clear();
            }
        }
    }

    for c in 0..cols {
        let mut run_start: Option<usize> = None;
        let mut run_cells: Vec<(usize, usize)> = Vec::new();
        for r in 0..=rows {
            if r < rows && grid[r][c] {
                if run_start.is_none() {
                    run_start = Some(r);
                    run_cells.clear();
                }
                run_cells.push((r, c));
            } else {
                if let Some(start_row) = run_start {
                    if run_cells.len() >= 2 {
                        for (pos, cell) in run_cells.iter().enumerate() {
                            cell_to_slots.entry(*cell).or_default().push((slot_id, pos));
                        }
                        slots.push(Slot {
                            id: slot_id,
                            direction: 'V',
                            start_row,
                            start_col: c,
                            length: run_cells.len(),
                            cells: run_cells.clone(),
                            intersections: Vec::new(),
                        });
                        slot_id += 1;
                    }
                }
                run_start = None;
                run_cells.clear();
            }
        }
    }

    for slot_refs in cell_to_slots.values() {
        if slot_refs.len() < 2 {
            continue;
        }
        for i in 0..slot_refs.len() {
            for j in (i + 1)..slot_refs.len() {
                let (sid_a, pos_a) = slot_refs[i];
                let (sid_b, pos_b) = slot_refs[j];
                slots[sid_a].intersections.push(Intersection {
                    other_slot_id: sid_b,
                    this_position: pos_a,
                    other_position: pos_b,
                });
                slots[sid_b].intersections.push(Intersection {
                    other_slot_id: sid_a,
                    this_position: pos_b,
                    other_position: pos_a,
                });
            }
        }
    }

    slots
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_slots_and_intersections() {
        let grid = vec![vec![true, true], vec![true, true]];
        let slots = extract_slots(&grid);
        assert_eq!(4, slots.len());
        assert!(slots.iter().all(|slot| !slot.intersections.is_empty()));
    }
}
