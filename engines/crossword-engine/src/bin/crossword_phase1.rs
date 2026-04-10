use clap::Parser;

#[derive(Parser, Debug)]
struct Args {
    #[arg(long)]
    size: usize,
    #[arg(long)]
    words: String,
    #[arg(long)]
    seed: u64,
    #[arg(long, default_value_t = 1)]
    preparation_attempts: usize,
}

fn main() {
    let args = Args::parse();
    match crossword_engine::engine::run_engine(
        args.size,
        &args.words,
        args.seed,
        args.preparation_attempts,
    ) {
        Ok(output) => {
            println!(
                "{}",
                serde_json::to_string(&output).expect("serialize engine output")
            );
        }
        Err(err) => {
            eprintln!("{err}");
            std::process::exit(1);
        }
    }
}
