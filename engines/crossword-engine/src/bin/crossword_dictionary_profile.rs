use clap::Parser;

#[derive(Parser, Debug)]
struct Args {
    #[arg(long)]
    words: String,
    #[arg(long)]
    output: Option<String>,
}

fn main() {
    let args = Args::parse();
    let output = args.output.unwrap_or_else(|| {
        crossword_engine::dictionary_profile::dictionary_profile_path(&args.words)
            .display()
            .to_string()
    });
    match crossword_engine::dictionary_profile::write_dictionary_profile(&args.words, &output) {
        Ok(_) => println!("{output}"),
        Err(err) => {
            eprintln!("{err}");
            std::process::exit(1);
        }
    }
}
