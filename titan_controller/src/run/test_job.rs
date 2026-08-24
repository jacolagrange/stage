use std::path::Path;
use std::fs::File;

pub fn job_succeed(job_path: &Path) -> bool {
    let mut good_job = false;
    if job_path.exists() {
        good_job |= test_non_empty_file(&job_path.join("sim.out"));
        good_job |= test_non_empty_file(&job_path.join("sim.stat.sqlite3"));
    }
    return good_job;
}

fn test_non_empty_file(sim_out_path: &Path) -> bool {
    let mut good_sim_out = false;
    if sim_out_path.exists() {
        if let Ok(file) = File::open(&sim_out_path){
            if let Ok(metadata) = file.metadata() {
                good_sim_out = metadata.len() > 0;
            }
        }
    }
    return good_sim_out;
}
