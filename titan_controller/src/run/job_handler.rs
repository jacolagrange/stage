use std::path::{Path, PathBuf};
use std::io::Write;
use std::fs::File;
use std::fs;

use super::status::JobStatus;
use super::parse_experiment::ParseExperiment;
use super::test_job;
use super::db::ExperimentsDataBase;
use crate::hpc::slurm_handle;
use crate::constants::{TEMP_FOLDER_NAME, EXECUTE_TEMPLATE, EXPERIMENT_DB_NAME };
use crate::communication::ssh;
use crate::run::{
    experiment::Experiment,
    benchmark_suite::BenchmarkSuite,
    parse_experiment
};
use crate::utils::fill_template::fill_template;

pub struct JobHandler {
    //TODO use a trait for hpc-handler
    hpc_handler: slurm_handle::SlurmHandler,
    temp_path: PathBuf

}

impl JobHandler{
    pub fn new(hpc_handler: slurm_handle::SlurmHandler) -> Self {
        let temp_path = TEMP_FOLDER_NAME.clone();
        if ! temp_path.is_dir(){
            let _ = fs::create_dir(&temp_path);
        }
        JobHandler{hpc_handler, temp_path}
    }

    pub fn submit_jobs(&self, experiment_path: &str, dry_run: &bool) -> Result<(), Box<dyn std::error::Error>> {
        //Get experiments parameters
        let experiment_path = Path::new(experiment_path);
        let parser = ParseExperiment::new(&experiment_path);
        let mut experiment = parser.get_arguments();

        let dst = parser.get_exp_dst();
        let _ = write_submit_job_map(&experiment, &dst);

        let mut cur_db = ExperimentsDataBase::new()?;

        //remove existing benchmarks
        if ! experiment.keep_state(&cur_db, &[JobStatus::TOSUBMIT, JobStatus::FAILED], &true) {
            println!("Experiment is already fully done, nothing to do... bye");
            return Ok(());
        }
        let _ = cur_db.add_new_experiment(&experiment);

        if ! dry_run {
            //Obtain a unique hash from the server
            let mut hashes = ssh::get_hash_titan(experiment.benchmark_suites.len())?.into_iter();

            for benchmark_suite in &mut experiment.benchmark_suites {
                self.submit_one_job(benchmark_suite, &hashes.next().unwrap(), &mut cur_db)?;
            }
            let _ = cur_db.set_experiment_status(&experiment, &JobStatus::SUBMITTED);
        }

        Ok(())
    }

    fn submit_one_job(&self, benchmark_suite: &BenchmarkSuite, hash: &str, cur_db: &mut ExperimentsDataBase) -> Result<(), std::io::Error> {
        //Create the job file first
        let job_file = format!("job_{hash}.sh");
        let job_file_path = self.temp_path.join(Path::new(&job_file));
        let _ = self.hpc_handler.create_job_file(benchmark_suite, &job_file_path).expect("Failed to create job file for suite {benchmark_suite.suite}");

        if let Some(job_nr) = self.hpc_handler.submit_job(&job_file_path)? {
            //Submission job suceeeded
            //Now create the experiment files
            let exp_dir = self.create_job_files(benchmark_suite, &job_nr, cur_db)?;
            self.hpc_handler.submit_experiment(&exp_dir);
            let _ = cur_db.set_bench_suite_job_id(&benchmark_suite, &job_nr, &Some(JobStatus::SUBMITTED));
            println!("Submitted job {} (jobid {})", &benchmark_suite.meta_arguments["<JOB>"], &job_nr);
        } else {
            println!("Not all the jobs could be subitted to titan. Run collect, to retry those jobs later");
        }
        Ok(())
    }

    //This file runs completely inside the VM, so is independent on the server infrastructure
    fn create_job_files(&self, job_argument: &BenchmarkSuite, job_nr: &str, cur_db: &mut ExperimentsDataBase) -> Result<PathBuf, std::io::Error> {
        let temp_exp_path = self.temp_path.join(Path::new(&job_nr));
        if ! temp_exp_path.is_dir() { let _ = fs::create_dir(&temp_exp_path); }

        let mut task_idx = 1;
        let bench_suite_dir = &job_argument.host_dst_path;
        for sim_param in &job_argument.simulator_parameters{
            let sim_dir = sim_param.get_dir(&bench_suite_dir);
            for bench_param in &sim_param.benchmark_parameters {
                let bench_param_dir = bench_param.get_dir(&sim_dir);
                for bench_run in &bench_param.benchmark_runs {
                    let bench_run_dir = bench_run.get_dir(&bench_param_dir);

                    let exp_file_path_str = format!("execute_{job_nr}_{task_idx}.sh");
                    let exp_file_path = Path::new(&exp_file_path_str);
                    let dst_exp_path = temp_exp_path.join(&exp_file_path);
                    fill_template(EXECUTE_TEMPLATE.to_owned(), &dst_exp_path, &bench_param.arguments);

                    let _ = cur_db.set_task_id(&bench_run_dir, &task_idx);

                    task_idx += 1;
                }
            }
        }
        Ok(temp_exp_path)
    }

    pub fn collect_jobs(&mut self, experiment_map_file: &Path) -> Result<(), Box<dyn std::error::Error>> {
        let mut experiment = parse_experiment::get_exp_map(&experiment_map_file).unwrap();
        let mut cur_db = ExperimentsDataBase::new()?;
        let results_dir = Self::results_dir(experiment_map_file);

        let titan_job_stats = self.hpc_handler.get_jobs_retry(false, None)?;
        let titan_job_ids: Vec<String> = titan_job_stats.iter().map(|stat| stat.get_job_id()).collect();

        let mut any_new_result = false;
        experiment.for_each_run_path(|path| {
            if Ok(Some(JobStatus::SUBMITTED)) != cur_db.get_status(&path) { return; }

            if let Ok(Some(bench_job_task_id)) = cur_db.get_job_task_format(&path){
                if !titan_job_ids.contains(&bench_job_task_id) {

                    let res = self.retrieve_result(&bench_job_task_id, &path);
                    match res {
                        Ok(true) => {
                            let _ = cur_db.set_status(&path, &JobStatus::DONE);
                            let _ = Self::link_result(&results_dir, &path);
                            any_new_result = true;
                        }
                        Ok(false) | Err(_) => {
                            println!("Could not retreive job_id {}.", &bench_job_task_id);
                            let _ = cur_db.set_status(&path, &JobStatus::FAILED);
                        }
                    }
                }
            }
        });

        if any_new_result {
            println!("Results available under: {}", results_dir.display());
        }

        //remove existing benchmarks
        if ! experiment.keep_state(&cur_db, &[JobStatus::TOSUBMIT, JobStatus::FAILED], &true) {
            println!("All experiment downloads succeeded");
        } else {
            self.retry_experiment(&experiment, &mut cur_db)?;
        }
        Ok(())
    }

    fn retrieve_result(&self, bench_job_id: &str, dst_path: &Path) -> Result<bool, std::io::Error> {
        let result_file = format!("results_{bench_job_id}.tar.gz");
        let src_path = format!("/home/slurmslave/results/{result_file}");
        let tar_file_path = self.temp_path.join(&result_file);
        
        let _ = ssh::get_files(&src_path, self.temp_path.to_str().unwrap())?;
        let _ = ssh::untar(&tar_file_path, &dst_path, true)?;

        if test_job::job_succeed(&dst_path) {
            println!("Experiment {bench_job_id} was successfully downloaded");
            Ok(true)
        } else {
            println!("Experiment {bench_job_id} did not pass the tests");
            let _ = ssh::clean_dir(&dst_path);
            Ok(false)
        }
    }

    // `experiment_map_file` is the same `--path` the user passed to --submit/
    // --collect (their own host_destination_path); results live in a
    // "results" subdirectory there, mirroring the benchmark/run-index
    // structure without exposing the internal cache-hash paths at all.
    fn results_dir(experiment_map_file: &Path) -> PathBuf {
        let base = if experiment_map_file.file_name().and_then(|f| f.to_str()) == Some(EXPERIMENT_DB_NAME) {
            experiment_map_file.parent().unwrap_or(experiment_map_file)
        } else {
            experiment_map_file
        };
        base.join("results")
    }

    // Symlinks the real (cache-hash-keyed) result directory into
    // results_dir/<benchmark_name>/<run_idx>/, so results are browsable at
    // a predictable, human-readable path instead of requiring readers to
    // parse experiments.json to find them.
    fn link_result(results_dir: &Path, real_path: &Path) -> std::io::Result<()> {
        let run_idx = match real_path.file_name() {
            Some(name) => name,
            None => return Ok(()),
        };
        let benchmark_name = match real_path.parent().and_then(|p| p.file_name()) {
            Some(name) => name,
            None => return Ok(()),
        };

        let link_path = results_dir.join(benchmark_name).join(run_idx);
        fs::create_dir_all(results_dir.join(benchmark_name))?;
        if link_path.exists() || link_path.is_symlink() {
            fs::remove_file(&link_path).or_else(|_| fs::remove_dir_all(&link_path))?;
        }
        std::os::unix::fs::symlink(real_path, &link_path)
    }

    fn retry_experiment(&self, experiment: &Experiment, cur_db: &mut ExperimentsDataBase) -> Result<(), std::io::Error> {
        let mut hashes = ssh::get_hash_titan(experiment.benchmark_suites.len())?.into_iter();
        println!("Failed or not yet submitted experiment detected... retyring/sending them.");

        for benchmark_suite in &experiment.benchmark_suites {
            self.submit_one_job(benchmark_suite, &hashes.next().unwrap(), cur_db)?;
        }
        Ok(())
    }
}

pub fn write_submit_job_map(experiment: &Experiment, host_dst_path: &Path) -> std::io::Result<()> {
    let _ = set_up_host_dir(host_dst_path);
    let file_path = host_dst_path.join(EXPERIMENT_DB_NAME);

    let file = File::create(file_path)?;
    let mut writer = std::io::BufWriter::new(file);
    let _ = serde_json::to_writer_pretty(&mut writer, experiment)?;
    writer.flush()?;
    Ok(())
}

pub fn set_up_host_dir(host_dst_path: &Path) -> Result<(), std::io::Error> {
        if ! (host_dst_path.exists() && host_dst_path.is_dir()) {
            std::fs::create_dir_all(&host_dst_path)?;
        }
        Ok(())
}
