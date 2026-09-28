use std::env;
use std::path::PathBuf;
use std::sync::LazyLock;

pub static JOB_TEMPLATE: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/script-template/job_docker.sh"));
pub static EXECUTE_TEMPLATE: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/script-template/execute_Sniper.sh"));

pub static EXPERIMENT_DB_NAME: &str = "experiments.json";
pub static CACHE_DB_NAME: &str = "job_info.sqlite3";
pub static SNIPER_ARGUMENT_FILE_NAME: &str = "args.json";
pub static TITAN_SUBMIT_DIR: &str = "/home/slurmslave/jobs/submitted/.";

pub static ID_FILE: LazyLock<PathBuf> = LazyLock::new(|| {
    let path = dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("titan_controller")
        .join(".id");

    if !path.exists() {
        eprintln!("[ERROR] Required ID file not found at: {}", path.display());
        std::process::exit(1);
    }

    path
});

pub static TEMP_FOLDER_NAME: LazyLock<PathBuf> = LazyLock::new(|| {
    let path = std::env::temp_dir().join(env!("CARGO_PKG_NAME"));
    println!("[INFO] Temp folder is set to: {}", path.display());

    path
});

pub static CACHE_FOLDER_NAME: LazyLock<PathBuf> = LazyLock::new(|| {
    let path = dirs::cache_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(env!("CARGO_PKG_NAME"));

    println!("[INFO] Cache folder is set to: {}", path.display());
    path
});

pub static LOCAL_SNIPER_DIR: LazyLock<PathBuf> = LazyLock::new(|| {
    if let Ok(env_path) = env::var("SNIPER_ROOT") {
        PathBuf::from(env_path)
    } else {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Documents/sniperAFS/sniper")
    }
});

pub static LOCAL_BENCHMARK_DIR: LazyLock<PathBuf> = LazyLock::new(|| {
    if let Ok(env_path) = env::var("BENCHMARK_ROOT") {
        PathBuf::from(env_path)
    } else {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Vault/benchmarks/benchmarks")
    }
});
