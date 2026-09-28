mod hpc;
mod communication;
mod manage;
mod run;
mod constants;
mod utils;

use clap::{Parser, ArgGroup};
use std::path::Path;

use crate::hpc::slurm_handle::SlurmHandler;
use crate::run::job_handler::JobHandler;
use crate::communication::credentials::Credentials;
use crate::constants::EXPERIMENT_DB_NAME;
use crate::utils::job_id::JobIds;

#[derive(Parser, Debug)]
#[command(author, version, about, long_about=None)]
#[command(group(
            ArgGroup::new("TitanCmd")
                .required(true)
                .args(["list", "submit", "delete", "collect"]),
        ))]
struct Args{
    #[arg(long, group="Glist", value_parser = HandleObject::parse_titan_obj)]
    list: Option<HandleObject>, 

    #[arg(long, group="Gsubmit", value_parser = HandleObject::parse_titan_obj)]
    submit: Option<HandleObject>,

    #[arg(long, group="Gdelete", value_parser = HandleObject::parse_titan_obj)]
    delete: Option<HandleObject>,

    #[arg(long, group="Gsubmit", value_parser = HandleObject::parse_titan_obj)]
    collect: Option<HandleObject>,

    // If we display all the information or restricted to the user. (Only valid with --list)
    #[arg(short, long, requires = "Glist")]
    all: bool,

    // If we display the completed jobs as well. (Only valid with --list jobs)
    #[arg(short, long, requires = "Glist")]
    completed: Option<usize>,

    // The jobid we wish to delete
    #[arg(short='i', long, requires = "Gdelete", value_parser = JobIds::parse_jobids)]
    jobid: Option<JobIds>,
    //jobid: Option<Vec<usize>>,

    // Path of the object we wish to upload/execute. In case of submit job, the first file will be
    // executed on sniper, and the second one (e.g. execute.sh, to run on the VM) will only be copied to the titan node.
    // (e.g usage ./bin -u job -p job.sh execute.sh)
    #[arg(short, long, requires = "Gsubmit")]
    path: Option<Vec<String>>,

    #[arg(short, long, requires = "Gdelete")]
    name: Option<String>,

    #[arg(long, requires = "Gsubmit")]
    dry: bool
}

#[derive(Clone, Debug)]
enum HandleObject {
    Job,
    VM,
    Trace
}

impl HandleObject {
    fn parse_titan_obj(arg: &str) -> Result<Self, String> {
        match arg.to_lowercase().as_str() {
            "job" => Ok(HandleObject::Job),
            "jobs" => Ok(HandleObject::Job),
            "vm" => Ok(HandleObject::VM),
            "vms" => Ok(HandleObject::VM),
            "trace" => Ok(HandleObject::Trace),
            "traces" => Ok(HandleObject::Trace),
            _ => Err(format!("{} is not a recognized option, choose: Job|VM|Trace", arg)),
        }
    }
}

pub fn main() {
    let args = Args::parse();

    let creds = Credentials::new().unwrap();

    if let Some(titan_obj) = args.list {
        //let titan_obj = HandleObject::validate_value_cli(titan_object, "LIST");
        match titan_obj {
            HandleObject::Job => {
                let hpc_handler = SlurmHandler::new(creds);
                //let job_handler = JobHandler::new(hpc_handler);
                let res = hpc_handler.print_jobs(args.all, args.completed);
                match res {
                    Ok(_) => {}
                    Err(e) => {
                        eprintln!("Error when retreiving the job list: {}", e);
                        std::process::exit(1);
                    }
                }
            }
            HandleObject::VM => {
                let res = manage::docker::list_dockerfiles();
                match res {
                    Ok(_) => {}
                    Err(e) => {
                        eprintln!("Error when retreiving the VMS list: {}", e);
                        std::process::exit(1);
                    }
                }
            }
            HandleObject::Trace => {
                let res = manage::traces::list_traces();
                match res {
                    Ok(_) => {}
                    Err(e) => {
                        eprintln!("Error when retreiving the traces: {}", e);
                        std::process::exit(1);
                    }
                }
            }
        }
    } else if let Some(titan_obj) = args.delete {
        //let titan_obj = HandleObject::validate_value_cli(titan_object, "DELETE");
        match titan_obj {
            HandleObject::Job => {
                if let Some(jobids) = args.jobid {
                    let hpc_handler = SlurmHandler::new(creds);
                    //let job_handler = JobHandler::new(hpc_handler);
                    let res = hpc_handler.delete_jobs(jobids.ids);
                    match res {
                        Ok(_) => {}
                        Err(e) => {
                            eprintln!("Error when deleting JOB: {}", e);
                            std::process::exit(1);
                        }
                    }
                }
            }
            HandleObject::VM => {
                let res = manage::docker::delete_dockerfile(&args.name.unwrap());
                match res {
                    Ok(_) => {}
                    Err(e) => {
                        eprintln!("Error when deleting vm: {}", e);
                        std::process::exit(1);
                    }
                }
            }
            _ => {
                println!("Unsupported feature"); //need to add script on bacchus itself -> to remove from all the nodes
            }
        }
    } else if let Some(titan_obj) = args.submit {
        match titan_obj {
            HandleObject::VM => {
                if let Some(paths) = args.path {
                    let dockerfile_path = Path::new(&paths[0]);
                    let res = manage::docker::upload_dockerfile(dockerfile_path);
                    match res {
                        Ok(_) => {}
                        Err(e) => {
                            eprintln!("Error when uploading dockerfile from path {}: {}", dockerfile_path.display(), e);
                            std::process::exit(1);
                        }
                    }
                }
            }
            HandleObject::Trace => {
                if let Some(paths) = args.path {
                    let trace_path = Path::new(&paths[0]);
                    let res = manage::traces::upload_trace(trace_path);
                    match res {
                        Ok(_) => {}
                        Err(e) => {
                            eprintln!("Error when uploading trace from path {}: {}", trace_path.display(), e);
                            std::process::exit(1);
                        }
                    }
                }
            }
            HandleObject::Job => {
                let hpc_handler = SlurmHandler::new(creds);
                let job_handler = JobHandler::new(hpc_handler);
                let paths = args.path.unwrap();
                if paths.len() >= 1 {
                    let experiment_path = &paths[0];
                    let res = job_handler.submit_jobs(experiment_path, &args.dry);
                    match res {
                        Ok(_) => {}
                        Err(e) => {
                            eprintln!("Error when submitting job: {}", e);
                            std::process::exit(1);
                        }
                    }
                } else {
                    eprintln!("You need to provide an experiment json file to run an experiment");
                }
            }
        }
    } else if let Some(titan_obj) = args.collect {
        match titan_obj {
            HandleObject::Job => {
                let hpc_handler = SlurmHandler::new(creds);
                let mut job_handler = JobHandler::new(hpc_handler);
                if let Some(paths) = args.path {
                    let experiment_map_path = Path::new(&paths[0]);
                    let res = job_handler.collect_jobs(&experiment_map_path);
                    match res {
                        Ok(_) => {}
                        Err(e) => {
                            eprintln!("Error when collecting jobs: {}", e);
                            std::process::exit(1);
                        }
                    }
                } else {
                    eprintln!("You need to provide a Path with a {} file to download the experiments", EXPERIMENT_DB_NAME);
                }
            }
            _ => {
                println!("Unsupported feature");
            }
        }
    }
}
