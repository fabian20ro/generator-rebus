pub mod incremental;
pub mod procedural;
pub mod validate;

pub use incremental::generate_incremental_template;
pub use procedural::generate_procedural_template;
pub use validate::{
    black_spacing_ok, count_edge_singletons, creates_single_letter, is_connected,
    placement_creates_invalid_slots, template_rejection_bucket, validate_template,
};

#[cfg(test)]
mod tests {
    use rand::{SeedableRng, rngs::StdRng};

    use super::{
        black_spacing_ok, count_edge_singletons, generate_procedural_template,
        placement_creates_invalid_slots, template_rejection_bucket, validate_template,
    };

    #[test]
    fn procedural_template_respects_constraints() {
        let mut rng = StdRng::seed_from_u64(42);
        let grid = generate_procedural_template(7, 4, 50, &mut rng).expect("grid");
        assert!(validate_template(&grid).is_ok());
    }

    #[test]
    fn second_row_black_can_be_valid_with_edge_singleton_cover() {
        let grid = vec![
            vec![true, true, true, true, true],
            vec![true, true, false, true, true],
            vec![true, true, true, true, true],
            vec![true, true, true, true, true],
            vec![true, true, true, true, true],
        ];

        assert!(validate_template(&grid).is_ok());
        assert_eq!(1, count_edge_singletons(&grid));
    }

    #[test]
    fn interior_singleton_still_rejected() {
        let grid = vec![
            vec![true, true, true, true, true],
            vec![true, true, false, true, true],
            vec![true, true, true, true, true],
            vec![true, true, false, true, true],
            vec![true, true, true, true, true],
        ];

        let err = validate_template(&grid).expect_err("interior singleton");
        assert_eq!("singleton_interior", template_rejection_bucket(&err));
    }

    #[test]
    fn spacing_blocks_orthogonal_neighbors_but_allows_diagonal() {
        let grid = vec![
            vec![true, true, true],
            vec![true, false, true],
            vec![true, true, true],
        ];

        assert!(!black_spacing_ok(&grid, 1, 0, 3));
        assert!(black_spacing_ok(&grid, 0, 0, 3));
    }

    #[test]
    fn placement_guard_allows_supported_edge_single() {
        let grid = vec![
            vec![true, true, true, true, true],
            vec![true, true, false, true, true],
            vec![true, true, true, true, true],
            vec![true, true, true, true, true],
            vec![true, true, true, true, true],
        ];

        assert!(!placement_creates_invalid_slots(&grid, 1, 2, 5));
    }

    #[test]
    fn placement_guard_rejects_uncovered_edge_single() {
        let grid = vec![
            vec![true, false, true],
            vec![false, true, true],
            vec![true, true, true],
        ];

        assert!(placement_creates_invalid_slots(&grid, 0, 1, 3));
    }
}
