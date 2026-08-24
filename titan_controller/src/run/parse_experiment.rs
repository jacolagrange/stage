use json;
use std::fs::File;
use std::io::Read;
use std::fmt::Write;
use std::path::{Path, PathBuf};
use std::collections::{HashMap, HashSet};
use crate::constants::EXPERIMENT_DB_NAME;
use crate::run::{
    experiment::Experiment,
    benchmark_suite::BenchmarkSuite,
    simulator_parameter::SimulatorParameter,
    benchmark_parameter::BenchmarkParameter,
    benchmark_run::BenchmarkRun
};

use std::str::FromStr;
use crate::run::caching;

#[derive(PartialEq)]
enum ExperimentType{
    BINARY,
    TRACE,
    PINBALL
}

impl FromStr for ExperimentType {
    type Err = ();
    fn from_str(input: &str) -> Result<ExperimentType, Self::Err> {
        match input.to_lowercase().as_str() {
            "binary" | "binaries" => Ok(ExperimentType::BINARY),
            "trace" | "traces" => Ok(ExperimentType::TRACE),
            "pinball" | "pinballs" => Ok(ExperimentType::PINBALL),
            _ => Err(())
        }
    }
}

enum VmType {
    DOCKER
}

#[derive(Debug)]
pub struct ParseExperiment{
    exp: json::JsonValue,
    bench_suites: json::JsonValue
}

impl ParseExperiment{
    pub fn new(exp_json_path: &Path) -> Self{
        let mut exp_json_data = String::new();
        File::open(exp_json_path).unwrap()
            .read_to_string(&mut exp_json_data)
            .unwrap();
        let exp = json::parse(&exp_json_data).unwrap();

        let mut combined_suites = json::JsonValue::new_array();
        for bench_location in exp["benchmarks"].members() {
            let mut bench_path = PathBuf::from(bench_location.to_string());
            if ! bench_path.is_absolute() {
                bench_path = exp_json_path.parent().unwrap().join(bench_path);
            }
            println!("Using benchmark json file {:?}", bench_path);

            let mut bench_json_data = String::new();
            File::open(bench_path).unwrap()
                .read_to_string(&mut bench_json_data)
                .unwrap();
            let bench = json::parse(&bench_json_data).unwrap();
            for suite in bench["suites"].members() {
                combined_suites.push(suite.clone()).unwrap();
            }
        }
        let bench_suites = json::object! {
            "suites" => combined_suites
        };

        Self {exp, bench_suites}
    }

    pub fn get_arguments(&self) -> Experiment {
        let mut benchmark_suites = Vec::new();
        let job_git_repos = self.get_git_repos(&self.exp);
        let job_mounts_docker: String = self.get_vm_mounts(&self.exp, VmType::DOCKER);

        for suite in self.bench_suites["suites"].members() {
            let mut git_repos = self.get_git_repos(suite);
            git_repos.extend(job_git_repos.clone());
            let git_repos_script = self.make_git_checkout_script(&git_repos);

            let mut mounts_docker: String = self.get_vm_mounts(suite, VmType::DOCKER);
            mounts_docker.push_str(&job_mounts_docker);

            let job_vals = &self.exp["job"];

            let nr_runs: usize = job_vals["runs"].as_usize().unwrap();
            //let total_jobs: usize = suite["benchmarks"].len() * self.get_number_configs() * nr_runs;

            let job_name = json_value_to_string(&job_vals["name"],"") + &format!("_{}", suite["suite"]);

            let meta_arguments = HashMap::from([
                // ("<ACCOUNT>",   String::new()),
                (String::from("<JOB>"),       job_name),
                (String::from("<CORES>"),     json_value_to_string(&job_vals["core_per_experiment"],"")),
                (String::from("<MEMORY>"),    self.get_tot_memory().to_string()),
                //(String::from("<TASKS>"),     total_jobs.to_string()),
                (String::from("<VM_NAME>"),   json_value_to_string(&job_vals["vm_name"],"")),
                (String::from("<GIT-REPOSITORIES>"), git_repos_script),
                (String::from("<DOCKER_MOUNTS>"),    mounts_docker),
            ]);

            let simulator_parameters = self.get_exp_arguments(suite, nr_runs);

            //let host_dst_path_str = json_value_to_string(&self.exp["host_destination_path"], "");
            //let host_dst_path = Path::new(&host_dst_path_str);

            let traces = self.get_traces_map(suite);
            let cache_path = caching::get_tools_hash_path(&git_repos, &traces);

            benchmark_suites.push(
                BenchmarkSuite{
                    suite: json_value_to_string(&suite["suite"],""),
                    meta_arguments, 
                    simulator_parameters,
                    host_dst_path: cache_path.to_path_buf()
                });
        }

        Experiment{benchmark_suites}
    }


    /*
     * Helper function to fill the JOB/Meta arguments
     */

    fn get_tot_memory(&self) -> usize {
        let core_str = json_value_to_string(&self.exp["job"]["core_per_experiment"],"");
        let mem_per_core_str = json_value_to_string(&self.exp["job"]["mem_per_core"],"");

        let core_nr: usize = core_str.parse().unwrap();
        let mem_per_core: usize = mem_per_core_str.parse().unwrap();

        core_nr * mem_per_core
    }

    fn get_git_repos(&self, args_obj: &json::JsonValue) -> HashMap<String, String> {
        let mut git_repos = HashMap::new();
        if args_obj.has_key("git") {
            for (repo, branch) in args_obj["git"].entries() {
                let repo_name = repo.strip_suffix("_branch").unwrap();
                let branch_str = branch.as_str().unwrap();
                git_repos.insert(repo_name.to_string(), branch_str.to_string());
            }
        }

        git_repos
    }

    fn make_git_checkout_script(&self, git_repos: &HashMap<String, String>) -> String {
        git_repos
            .iter()
            .map(|(repo, branch)| format!("checkout_git_repo {repo} {branch}\n"))
            .collect()
    }

    fn get_traces_map(&self, benchmark_suite: &json::JsonValue) -> Option<HashMap<String, String>> {
        let experiment_type = ExperimentType::from_str(&json_value_to_string(&benchmark_suite["type"],"")).unwrap();
        if experiment_type == ExperimentType::TRACE {
            let mut trace_map = HashMap::new();
            trace_map.insert(json_value_to_string(&benchmark_suite["suite"], ""), json_value_to_string(&benchmark_suite["version"], ""));
            Some(trace_map)
        } else {
            None
        }
    }

    fn get_vm_mounts(&self, args_obj: &json::JsonValue, vm_type: VmType) -> String {
        let mut vm_mounts = String::new();
        //First mount the git repos
        if args_obj.has_key("git"){
            for (repo, _) in args_obj["git"].entries() {
                let repo_name = repo.strip_suffix("_branch").unwrap();
                let repo_name_upper = repo_name.to_uppercase();
                let mount_str: String = match vm_type{
                    VmType::DOCKER => format!("-v /home/slurmslave/{repo_name}/${{{repo_name_upper}_GIT_ID}}:${{{repo_name}_mount}} \\\n\t"),
                };
                let _ = write!(vm_mounts, "{}", mount_str);
            }
        }

        //Mount additional repos
        if args_obj.has_key("vm_mount") {
            for (mnt_name, host_path) in args_obj["vm_mount"].entries() {
                let host_path_str = host_path.as_str().unwrap();
                if host_path_str.to_lowercase() == "none" {continue;}
                let mount_str: String = match vm_type{
                    VmType::DOCKER => format!("-v {host_path}:${{{mnt_name}}} \\\n\t"),
                };
                let _ = write!(vm_mounts, "{}", mount_str);
            }
        }

        vm_mounts
    }

    /*
     * Function to process all the arguments for the benchmark themselves
     */
    fn get_exp_arguments(&self, benchmark_suite: &json::JsonValue, nr_runs: usize) -> Vec<SimulatorParameter> {
        let mut exp_arguments: Vec<SimulatorParameter> = Vec::new();
        let sniper_arg_keys = self.get_sniper_argument_keys();
        let sniper_arg_maps = self.get_sniper_arguments(&sniper_arg_keys);
        let mut sniper_str_arguments = json_value_to_string(&self.exp["sniper_parameters"]["arguments"], " ");
        sniper_str_arguments.push(' ');
        let benchmarks_arguments = self.get_benchmark_arguments(benchmark_suite, nr_runs);

        //let host_job_exp_meta_info_path_str = json_value_to_string(&self.exp["host_destination_path"], "");
        //let host_job_exp_meta_info_path = Path::new(&host_job_exp_meta_info_path_str);
        
        for sniper_arg in &sniper_arg_maps {
            let mut sniper_str_arg: String = sniper_str_arguments.clone();
            for (key, val) in sniper_arg {sniper_str_arg = sniper_str_arg.replace(&*format!("{{{key}}}"), val);}

            let mut benchmark_parameters = benchmarks_arguments.clone();
            for benchmark_parameter in &mut benchmark_parameters {
                benchmark_parameter.arguments.get_mut("<ARGUMENTS>").unwrap().insert_str(0, &sniper_str_arg);
            }

            //let sniper_dir_name = self.get_sniper_dir_name(&sniper_arg_keys, sniper_arg);
            let simulator_dir_name = caching::get_hash_sniper_config(&sniper_str_arg).to_string();
            
            exp_arguments.push(
                SimulatorParameter{
                    simulator_dir_name,
                    variable_sniper_parameters: sniper_arg.clone(),
                    benchmark_parameters
                });
        }

        exp_arguments
    }

    fn get_benchmark_arguments(&self, benchmark_suite: &json::JsonValue, nr_runs: usize) -> Vec<BenchmarkParameter> {
        let mut benchmark_arguments = Vec::new();

        let benchmark_parameters = json_value_to_string(&benchmark_suite["sniper_args"], " ");
        //let is_binary = json_value_to_string(&benchmark_suite["type"],"") == "binaries";
        let experiment_type = ExperimentType::from_str(&json_value_to_string(&benchmark_suite["type"],"")).unwrap();
        let suite_path = json_value_to_string(&benchmark_suite["suite_path"],"");
        let suit_build = if benchmark_suite.has_key("build_cmd") {
            Some(format!("\"{}\"", json_value_to_string(&benchmark_suite["build_cmd"], " ")))
        } else { None };

        for benchmark in benchmark_suite["benchmarks"].members(){
            let benchmark_path = if benchmark.has_key("bench_path") {
                format!("{suite_path}/{}", json_value_to_string(&benchmark["bench_path"],""))
            } else {
                suite_path.to_string()
            };

            let mut benchmark_build_path = &benchmark_path;

            let benchmark_build: String = if benchmark.has_key("build_cmd") {
                format!("\"{}\"", json_value_to_string(&benchmark["build_cmd"], " "))
            } else if let Some(build_str) = &suit_build { //Default to suite-path to build benchmark if not present for benchmark
                benchmark_build_path = &suite_path;
                build_str.clone()
            } else {
                String::from("make")
            };

            let setup_cmd = if benchmark.has_key("setup_cmd") {
                json_value_to_string(&benchmark["setup_cmd"], "\n")
            } else {
                String::new()
            };

            let benchmark_str = match experiment_type {
                ExperimentType::BINARY => {
                    let binary_str = json_value_to_string(&benchmark["binary"],"");
                    let benchmark_args = json_value_to_string(&benchmark["arguments"], " ");
                    format!("-- ${{BENCHMARKS_DIR}}/{benchmark_path}/{binary_str} {benchmark_args}")
                } 
                ExperimentType::TRACE => {
                    let mut trace_vec = Vec::new();
                    for trace_str in benchmark["traces"].members(){
                        trace_vec.push(format!("${{TRACES_DIR}}/{benchmark_path}/{}", json_value_to_string(trace_str,"")));
                    }
                    let traces_string = trace_vec.join(",");
                    format!("--traces={traces_string}")
                }
                ExperimentType::PINBALL => {
                    let mut trace_vec = Vec::new();
                    for trace_str in benchmark["pinballs"].members(){
                        trace_vec.push(format!("${{TRACES_DIR}}/{benchmark_path}/{}", json_value_to_string(trace_str,"")));
                    }
                    let traces_string = trace_vec.join(",");
                    format!("--pinballs={traces_string}")
                }
            };

            let benchmark_name = json_value_to_string(&benchmark["name"], "");

            let all_arguments = format!("{benchmark_parameters} {benchmark_str}");

            let arguments = HashMap::from([
                (String::from("<BENCH_BUILD_DIR>"), benchmark_build_path.clone()),
                (String::from("<BUILD_COMMAND>"), benchmark_build.clone()),
                (String::from("<SETUP_CMD>"), setup_cmd.clone()),
                (String::from("<ARGUMENTS>"), all_arguments.clone())
            ]);

            let benchmark_runs: Vec<BenchmarkRun> = (0..nr_runs)
                .map(|run_idx| BenchmarkRun { run_idx })
                .collect();

            benchmark_arguments.push( BenchmarkParameter{ arguments, benchmark_name, benchmark_runs });
        }

        benchmark_arguments
    }

    // Get the key of the first parameter-set, assuming those are present in the other sets as well
    // Returns a vector of all the keys of the parameter-values.
    fn get_sniper_argument_keys(&self) -> Vec<String> {
        //Assume there is at least one element in the "parameters" array
        let param_values = &self.exp["sniper_parameters"]["parameters"][0]["values"];
        let mut keys: Vec<String> = Vec::new();
        for (key, _) in param_values.entries() {keys.push(key.to_string());}

        keys
    }

    // Makes a vector of all the combinations of key-value pairs (i.e. all the experiments) that
    // sniper needs to run. This is done according to the mix-value
    fn get_sniper_arguments(&self, sniper_arg_keys: &[String]) -> Vec<HashMap<String, String>> {
        let mut sniper_args = Vec::new();
        let mut seen = HashSet::new();

        for param_value in self.exp["sniper_parameters"]["parameters"].members() {
            let mut param_combinations = match json_value_to_string(&param_value["mix"], "").to_lowercase().as_str() {
                "product" => create_parameter_product_mix(sniper_arg_keys, &param_value["values"]),
                "single" => create_parameter_single_mix(sniper_arg_keys, &param_value["values"]),
                _ => Vec::<Vec<String>>::new(),
            };

            if param_value["include_first"] == "false" && param_combinations.len() > 0{
                param_combinations.remove(0);
            }

            for combination in param_combinations {
                if seen.insert(combination.clone()) {
                    let arg: HashMap<String, String> = sniper_arg_keys
                        .iter()
                        .cloned()
                        .zip(combination)
                        .collect();
                    sniper_args.push(arg);
                }
            }
        }

        // Default to 1 single run with no parameter substitution if parameters are emtpy
        if sniper_args.is_empty() {
            sniper_args.push(HashMap::new());
        }

        sniper_args
    }

    #[allow(dead_code)]
    fn get_sniper_dir_name(&self, sniper_arg_keys: &Vec<String>, sniper_arg_vals: &HashMap<String, String>) -> String {
        let mut sniper_dir_vals: Vec<&str> = Vec::new();
        for key in sniper_arg_keys {
            sniper_dir_vals.push(&sniper_arg_vals[key]);
        }

        sniper_dir_vals.join("_")
    }


    #[allow(dead_code)]
    pub fn get_exp_dst(&self) -> PathBuf {
        let host_dst_path_str = json_value_to_string(&self.exp["host_destination_path"], "");
        Path::new(&host_dst_path_str).to_path_buf()
    }
}

/* Function that creates all the possible combinations of values
 * Output is a vector of each combination, and a combination is a vector of the string combinations
 * Creates a list of the last key, then passes the array back. The previous key (one before last),
 * will append the last key values to its own values. Rince and repeat, until the first key is met.
 * The first element of the VEC is the base configuration
 */
fn create_parameter_product_mix(keys: &[String], values: &json::JsonValue) ->Vec<Vec<String>> {
    if keys.is_empty() {return Vec::new();}

    let key = &keys[0];
    let mut combinations = Vec::new();
    if keys.len() == 1 {
        for key_value in values[key].members() {
            combinations.push(vec![json_value_to_string(key_value, "")]);
        }
    } else {
        let next_values = create_parameter_product_mix(&keys[1..], values);

        for key_value in values[key].members() {
            for next_value in &next_values {
                let mut val = vec![json_value_to_string(key_value, "")];
                val.append(&mut next_value.clone());
                combinations.push(val);
            }
        }
    }

    combinations
}

fn create_parameter_single_mix(keys: &[String], values: &json::JsonValue) ->Vec<Vec<String>> {
    if keys.is_empty() {return Vec::new();}

    let mut combinations = Vec::new();
    let mut default_values = Vec::new();
    for key in keys {
        default_values.push(json_value_to_string(&values[key][0], ""));
    }
    combinations.push(default_values.clone());

    for (i, key) in keys.iter().enumerate() {
        let mut new_combination = default_values.clone();
        for key_value in values[key].members().skip(1) {
            new_combination[i] = json_value_to_string(key_value, "");
            combinations.push(new_combination.clone());
        }
    }

    combinations
}

fn json_value_to_string(json_val: &json::JsonValue, separator: &str) -> String {
    match json_val {
        json::JsonValue::Array(array) => {
            array.iter().map(|x| json_value_to_string(x,"")).collect::<Vec<String>>().join(separator)
        },
        json::JsonValue::Number(_number) => {json_val.as_isize().unwrap().to_string()},
        json::JsonValue::String(string) => {string.clone()},
        json::JsonValue::Short(short) => {short.as_str().to_string()}
        json::JsonValue::Boolean(bool_val) => {bool_val.to_string()}
        _ => {String::new()}
    }
}

pub fn get_exp_map(job_map_path: &Path) -> std::io::Result<Experiment> {
    let file_path = if job_map_path.file_name().unwrap().to_str().unwrap() != EXPERIMENT_DB_NAME {
        job_map_path.join(EXPERIMENT_DB_NAME)
    } else {
        job_map_path.to_path_buf()
    };

    let file = File::open(&file_path)?;
    let mut reader = std::io::BufReader::new(file);
    let job_arguments: Experiment = serde_json::from_reader(&mut reader)?;
    Ok(job_arguments)
}
