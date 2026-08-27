use std::str::FromStr;
use std::path::Path;
use regex::Regex;
use lazy_static::lazy_static;
use time::{OffsetDateTime, Duration, format_description};

use crate::utils::fill_template::fill_template;
use crate::communication::{ssh, credentials::Credentials};
use crate::run::benchmark_suite::BenchmarkSuite;
use crate::constants::{JOB_TEMPLATE, TITAN_SUBMIT_DIR};


#[derive(Debug)]
pub struct SlurmJobStat {
    job_id: String,
    name: String,
    account: String,
    cores: usize,
    time: String,
    state: String
}

impl SlurmJobStat {
    pub fn get_job_id(&self) -> String {self.job_id.clone()}
}

pub struct SlurmHandler{
    creds: Credentials,
}

impl SlurmHandler{
    pub fn new(creds: Credentials) -> Self {
        SlurmHandler{creds}
    }

    fn get_jobs(&self, all: bool, completed: Option<usize>) -> Result<Option<Vec<SlurmJobStat>>, std::io::Error> {
        let (stdout, skip_nr) = 
        if let Some(days) = completed {
            lazy_static! {
                static ref SPACES: Regex = Regex::new(" +").unwrap();
            }
            let mut command = String::from("sacct");
            if !all {
                command += &(" --account=".to_owned() + &self.creds.username);
            }
            let start_time = OffsetDateTime::now_utc().checked_sub(Duration::days(days as i64)).unwrap();
            let sacct_time_format = format_description::parse_borrowed::<3>("[year]-[month]-[day]").unwrap();
            command += &format!(" -S {} --format=JobID,JobName%150,Account,NCPUS,Submit,State%30", start_time.format(&sacct_time_format).unwrap());
            let (mut stdout, _stderr) = ssh::send_command(&command)?;
            stdout = SPACES.replace_all(&stdout, r" ").to_string();
            (stdout, 2)
        } else {
            let mut command = String::from("squeue");
            if !all {
                command += &(" -A ".to_owned() + &self.creds.username);
            }
            command += " -o \"%i %j %a %C %M %R\"";
            let (stdout, _stderr) = ssh::send_command(&command)?;
            (stdout, 1)
        };

        if ! stdout.to_lowercase().contains("jobid") {
            //Something went wrong at the SSH
            println!("Could not find any Jobid in the output. This is the return output:\n stdout: {}", stdout);
            return Ok(None)
        }
    
        let output = stdout.split("\n").skip(skip_nr);
    
        let mut jobs = Vec::new();
        for line in output {
            let inputs: Vec<&str> = line.split(" ").collect();
            if inputs.len() < 5 || inputs[0].contains(".") {continue;}
            let t = SlurmJobStat{
                job_id: String::from(inputs[0]),
                name: String::from(inputs[1]),
                account: String::from(inputs[2]),
                cores: usize::from_str(inputs[3]).unwrap(),
                time: String::from(inputs[4]),
                state: String::from(inputs[5])
            };
            jobs.push(t);
        }
        Ok(Some(jobs))
    }

    pub fn get_jobs_retry(&self, all: bool, completed: Option<usize>) -> Result<Vec<SlurmJobStat>, std::io::Error> {
        let max_tries = 5;
        let mut last_err = None;

        for attempt in 0..max_tries {
            if attempt > 0 {
                // squeue/slurmctld hiccups (e.g. "slurm_load_jobs error: Unexpected
                // message received") are transient -- give the controller a moment
                // before hammering it again.
                std::thread::sleep(std::time::Duration::from_secs(5));
            }
            match self.get_jobs(all, completed) {
                Ok(Some(jobs)) => return Ok(jobs),
                Ok(None) => continue,
                Err(e) => last_err = Some(e),
            }
        }
        Err(last_err.unwrap_or_else(|| std::io::Error::new(
            std::io::ErrorKind::Other,
            "Could not get the jobs after multiple tries, something is wrong with job connection. Aborting here",
        )))
    }

    pub fn print_jobs(&self, all: bool, completed: Option<usize>) -> Result<(), std::io::Error> {
        let jobs = self.get_jobs_retry(all, completed)?;
        println!("{:<10} {:<100} {:<15} {:<5} {:<20} {:<15}", "JOBID", "NAME", "ACCOUNT", "CORES", "TIME", "STATE");
        println!("{:-<10} {:-<100} {:-<15} {:-<5} {:-<20} {:-<15}", "", "", "", "", "", "");
        for job in jobs {
            println!("{:<10} {:<100} {:<15} {:<5} {:<20} {:<15}", job.job_id, job.name, job.account, job.cores, job.time, job.state);
        }
        Ok(())
    }

    //TODO use something else than the python script
    pub fn delete_jobs(&self, job_ids: Vec<usize>) -> Result<(), std::io::Error> {
        for job_id in job_ids {
            let (stdout, stderr) = ssh::send_command(&format!("/home/slurmadmin/scripts/delete_job.py -j {} -u {} -p {}", job_id, self.creds.username, self.creds.password))?;
            if stdout.len() > 0 {println!("{}", stdout);}
            if stderr.len() > 0 {println!("{}", stderr);}
        }
        Ok(())
    }

    // Job-file is dependend on the infrastructure (slurm on titan for example)
    pub fn create_job_file(&self, job_argument: &BenchmarkSuite, job_file_path: &Path) -> Result<(), std::io::Error> {
        let _ = job_argument.prepare_host_directories();

        let nr_tasks = job_argument.get_number_task();
        let mut meta_arguments = job_argument.meta_arguments.clone();

        if ! meta_arguments.contains_key("<TASKS>") {
            meta_arguments.insert(String::from("<TASKS>"), nr_tasks.to_string());
        } else {
            *meta_arguments.get_mut("<TASKS>").unwrap() = nr_tasks.to_string();
        }

        if ! meta_arguments.contains_key("<ACCOUNT>") {
            meta_arguments.insert(String::from("<ACCOUNT>"), self.creds.username.clone());
        }

        //Create job file
        fill_template(JOB_TEMPLATE.to_owned(), &job_file_path , &meta_arguments);

        Ok(())
    }

    //TODO Check if those benchmarks are submitted already
    // Returns Job_nr on success, None otherwise
    pub fn submit_job(&self, job_file_path: &Path) -> Result<Option<String>, std::io::Error> {
        //First send the job file to titan
        let titan_job_path = Path::new(TITAN_SUBMIT_DIR).join(job_file_path.file_name().unwrap().to_str().unwrap());
        let _ = ssh::send_files(&job_file_path.to_str().unwrap(), &titan_job_path.to_str().unwrap())?;

        //Issue command to run the job-file
        let (stdout, stderr) = ssh::send_command(&format!("sbatch {}", titan_job_path.to_str().unwrap()))?;
        //Sbatch is send, the job-file is not needed anymore
        let _ = ssh::send_command(&format!("rm {}", titan_job_path.to_str().unwrap()))?;
        
        let job_nr = stdout.split("\n").next().unwrap().split(" ").last().unwrap();

        if stdout.contains("Submitted batch job") {
            Ok(Some(job_nr.to_string()))
        //} else if stdout.contains("Batch job submission failed: Resource temporarily unavailable") {
        //    eprintln!("Job submission did not produce a job-nr \nOutput:\n{stdout}\n\nErr:\n{stderr}");
        //    false
        } else {
            eprintln!("Job submission did not produce a job-nr \nOutput:\n{stdout}\n\nErr:\n{stderr}");
            Ok(None)
        }
    }

    /*
     * Takes a vector of experiments, and apply the template to them and finally sends all the
     * files to Titan.
     */
    pub fn submit_experiment(&self, exp_path: &Path) {
        let titan_path = Path::new(TITAN_SUBMIT_DIR);
        let _ = ssh::send_files(&exp_path.join(Path::new("*")).to_str().unwrap(), &titan_path.to_str().unwrap());
    }
}
