use serde::{Serialize, Deserialize};
use std::collections::HashMap;
use std::fs::File;
use std::io::Write as IoWrite;
use std::path::{Path, PathBuf};

use crate::constants::SNIPER_ARGUMENT_FILE_NAME;
use super::benchmark_parameter::BenchmarkParameter;
use super::status::JobStatus;
use super::db::ExperimentsDataBase;

/*
 * An experiment is defined for a given (sniper) configuration with defined parameters
 */
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct SimulatorParameter{
    pub simulator_dir_name: String,
    pub variable_sniper_parameters: HashMap<String, String>,
    pub benchmark_parameters: Vec<BenchmarkParameter>,
}

impl SimulatorParameter {
    pub fn get_dir(&self, dst_path: &Path) -> PathBuf {
        dst_path.join(&self.simulator_dir_name)
    }

    pub fn set_up_host_dir(&self, dst_path: &Path) -> std::io::Result<()> {
        let exp_meta_info_path = self.get_dir(dst_path);
        if ! (exp_meta_info_path.exists() && exp_meta_info_path.is_dir()) {
            std::fs::create_dir_all(&exp_meta_info_path)?;
        }
        self.create_host_argument_file(&exp_meta_info_path)?;

        for benchmark_parameter_argument in &self.benchmark_parameters {
            benchmark_parameter_argument.set_up_dir(&exp_meta_info_path)?;
        }

        Ok(())
    }

    //TODO remove this? This will be overwritten in the cache...
    fn create_host_argument_file(&self, dst_path: &PathBuf) -> std::io::Result<()>{
        let file = File::create(dst_path.join(SNIPER_ARGUMENT_FILE_NAME))?;
        let mut writer = std::io::BufWriter::new(file);
        let _ = serde_json::to_writer_pretty(&mut writer, &self.variable_sniper_parameters)?;
        writer.flush()?;
        Ok(())
    }

    pub fn get_number_task(&self) -> usize {
        let mut tasks = 0;
        for benchmark_parameter in &self.benchmark_parameters {
            tasks += benchmark_parameter.get_number_task();
        }
        tasks
    }

    // pub fn keep_task(&mut self, statuses: &[JobStatus]){
    //     self.benchmark_parameters.retain(|benchmark_parameter| statuses.contains(&benchmark_parameter.status));
    // }

    pub fn keep_state(&mut self, db: &ExperimentsDataBase, dst_path: &Path, statuses: &[JobStatus], include_none: &bool) -> bool {
        let sim_path = self.get_dir(&dst_path);
        self.benchmark_parameters.retain_mut(|bench_param| bench_param.keep_state(&db, &sim_path, &statuses, &include_none));
        self.benchmark_parameters.len() > 0
    }


    // pub fn change_state_benchmarks(&mut self, old_state: &Option<JobStatus>, new_state: &JobStatus){
    //     for benchmark_parameter in &mut self.benchmark_parameters{
    //         if old_state.is_none() || Some(&benchmark_parameter.status) == old_state.as_ref() {
    //             benchmark_parameter.status = new_state.clone();
    //         }
    //     }
    // }

    pub fn for_each_run_path<F>(&self, base_path: &Path, f: &mut F)
    where
        F: FnMut(&Path),
    {
        let sim_path = base_path.join(&self.simulator_dir_name);
        for bench in &self.benchmark_parameters {
            bench.for_each_run_path(&sim_path, f);
        }
    }
}
