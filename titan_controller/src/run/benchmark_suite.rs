use serde::{Serialize, Deserialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::simulator_parameter::SimulatorParameter;
use super::status::JobStatus;
use super::db::ExperimentsDataBase;

/*
 * A BenchmarkSuite is a the whole collections of all the different experiments (There is one job / benchmark
 * suite, because of different mounting and git requirements.)
 */
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct BenchmarkSuite {
    pub suite: String,
    pub meta_arguments: HashMap<String, String>,
    pub simulator_parameters: Vec<SimulatorParameter>,
    pub host_dst_path: PathBuf
}

impl BenchmarkSuite {
    pub fn prepare_host_directories(&self) -> std::io::Result<()> {
        for simulator_parameter in &self.simulator_parameters {
            simulator_parameter.set_up_host_dir(&self.host_dst_path)?;
        }
        Ok(())
    }

    pub fn get_number_task(&self) -> usize {
        let mut tasks = 0;
        for simulator_parameter in &self.simulator_parameters {
            tasks += simulator_parameter.get_number_task();
        }
        tasks
    }

    // pub fn keep_tasks(&mut self, statuses: &[JobStatus]){
    //     for simulator_parameter in &mut self.simulator_parameters{
    //         simulator_parameter.keep_task(statuses);
    //     }
    //     self.simulator_parameters.retain(|simulator_parameter| simulator_parameter.benchmark_parameters.len() > 0);
    // }

    pub fn keep_state(&mut self, db: &ExperimentsDataBase, statuses: &[JobStatus], include_none: &bool) -> bool {
        self.simulator_parameters.retain_mut(|sim_param| sim_param.keep_state(&db, &self.host_dst_path, &statuses, &include_none));
        self.simulator_parameters.len() > 0
    }


    // pub fn change_state_benchmarks(&mut self, old_state: &Option<JobStatus>, new_state: &JobStatus){
    //     for simulator_parameter in &mut self.simulator_parameters{
    //         simulator_parameter.change_state_benchmarks(old_state, new_state);
    //     }
    // }
    
    pub fn for_each_run_path<F>(&self, f: &mut F)
    where
        F: FnMut(&Path),
    {
        let base_path = &self.host_dst_path;
        for sim in &self.simulator_parameters {
            sim.for_each_run_path(base_path, f);
        }
    }
}
