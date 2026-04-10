pub mod incremental;
pub mod procedural;
pub mod validate;

pub use incremental::generate_incremental_template;
pub use procedural::generate_procedural_template;
pub use validate::{
    black_spacing_ok,
    creates_single_letter,
    is_connected,
    validate_template,
};

#[cfg(test)]
mod tests {
    use rand::{rngs::StdRng, SeedableRng};

    use super::{generate_procedural_template, validate_template};

    #[test]
    fn procedural_template_respects_constraints() {
        let mut rng = StdRng::seed_from_u64(42);
        let grid = generate_procedural_template(7, 4, 50, &mut rng).expect("grid");
        assert!(validate_template(&grid).is_ok());
    }
}
