use titan_controller::run::caching::get_hash_sniper_config; // Adjust path if needed
use std::env;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 2 {
        eprintln!("Usage: get_hash \"<cleaned-args>\"");
        std::process::exit(1);
    }
    let hash = get_hash_sniper_config(&args[1]);
    println!("{}", hash);
}
