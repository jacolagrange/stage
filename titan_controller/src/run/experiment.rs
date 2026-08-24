use serde::{Serialize, Deserialize};
use std::path::Path;

use super::benchmark_suite::BenchmarkSuite;
use super::status::JobStatus;
use super::db::ExperimentsDataBase;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Experiment {
    pub benchmark_suites: Vec<BenchmarkSuite>
}

impl Experiment {
    pub fn keep_state(&mut self, db: &ExperimentsDataBase, statuses: &[JobStatus], include_none: &bool) -> bool {
        self.benchmark_suites.retain_mut(|bench_suite| bench_suite.keep_state(&db, &statuses, &include_none));
        self.benchmark_suites.len() > 0
    }

    pub fn for_each_run_path<F>(&self, mut f: F)
    where
        F: FnMut(&Path),
    {
        for suite in &self.benchmark_suites {
            suite.for_each_run_path(&mut f);
        }
    }
}
