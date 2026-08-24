use serde::{Serialize, Deserialize};
use std::path::{Path, PathBuf};

use super::status::JobStatus;
use super::db::ExperimentsDataBase;

/*
 * A Benchmark is the based on the Experiment, all the inputs it needs to be run with
 */ 
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct BenchmarkRun{
    pub run_idx: usize,
}

impl BenchmarkRun {
    pub fn get_dir(&self, dst_path: &Path) -> PathBuf {
        dst_path.join(&self.run_idx.to_string())
    }

    pub fn set_up_dir(&self, dst_path: &Path) -> std::io::Result<()> {
        let p = self.get_dir(dst_path);
        if ! (p.exists() && p.is_dir()) {
            std::fs::create_dir_all(&p)?;
        }
        Ok(())
    }

    pub fn keep_state(&self, db: &ExperimentsDataBase, dst_path: &Path, statuses: &[JobStatus], include_none: &bool) -> bool {
        let benchmark_path = self.get_dir(dst_path); 
        match db.get_status(&benchmark_path){
            Ok(Some(status)) => statuses.contains(&status),
            _ => *include_none,
        }
    }

    pub fn for_each_run_path<F>(&self, base_path: &Path, f: &mut F)
    where
        F: FnMut(&Path),
    {
        let run_path = base_path.join(self.run_idx.to_string());
        f(&run_path);
    }

}
