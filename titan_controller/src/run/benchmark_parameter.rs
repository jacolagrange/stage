use serde::{Serialize, Deserialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::status::JobStatus;
//use super::db::TaskData;
use super::db::ExperimentsDataBase;
use super::benchmark_run::BenchmarkRun;

/*
 * A Benchmark is the based on the Experiment, all the inputs it needs to be run with
 */ 
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct BenchmarkParameter{
    pub arguments: HashMap<String, String>,
    pub benchmark_name: String,
    pub benchmark_runs: Vec<BenchmarkRun>,
}

impl BenchmarkParameter {
    pub fn get_dir(&self, dst_path: &Path) -> PathBuf {
        dst_path.join(&self.benchmark_name)
    }

    pub fn set_up_dir(&self, dst_path: &Path) -> std::io::Result<()> {
        let benchmark_path = self.get_dir(dst_path);
        if ! (benchmark_path.exists() && benchmark_path.is_dir()) {
            std::fs::create_dir_all(&benchmark_path)?;
        }

        for bench_run in &self.benchmark_runs {
            let _ = bench_run.set_up_dir(&benchmark_path);
        }

        Ok(())
    }

    pub fn get_number_task(&self) -> usize {
        self.benchmark_runs.len()
    }

    pub fn keep_state(&mut self, db: &ExperimentsDataBase, dst_path: &Path, statuses: &[JobStatus], include_none: &bool) -> bool {
        let bench_param_path = self.get_dir(&dst_path);
        self.benchmark_runs.retain_mut(|bench_run| bench_run.keep_state(&db, &bench_param_path, &statuses, &include_none));
        self.benchmark_runs.len() > 0
    }

    pub fn for_each_run_path<F>(&self, base_path: &Path, f: &mut F)
    where
        F: FnMut(&Path),
    {
        let bench_path = base_path.join(&self.benchmark_name);
        for run in &self.benchmark_runs {
            run.for_each_run_path(&bench_path, f);
        }
    }
}
