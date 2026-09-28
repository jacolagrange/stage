# TitanController

> **For the actual working setup on this Titan cluster** — how to submit,
> where results land, how to add/modify a benchmark — see
> [RUNBOOK.md](RUNBOOK.md). This README covers generic install/config and
> the tool's original git-checkout-based design; the runbook documents
> what we actually use instead and why.

## Installation

> **Note:** This tool has primarily been tested on Linux. There is no obvious reason why it wouldn't work on other operating systems, but unexpected issues may occur.

### Dependencies

1. **SQLite C Development Libraries:**
   Make sure you have SQLite installed on your system.
   * **Ubuntu / Debian:** `sudo apt install libsqlite3-dev pkg-config`
   * **Alternative:** You can build without system libraries by enabling the `bundled` feature in `rusqlite`:
     ```bash
     cargo add rusqlite --features bundled
     ```

2. **Rust Toolchain:**
   You will need `rustc` and `cargo` installed to build the project and handle dependencies.

---

## Configuration

### 1. Configuration File (`.id`)
This application looks for its `.id` configuration file in your system's user configuration directory:

* **Linux:** `~/.config/titan_controller/.id`
* **macOS:** `~/Library/Application Support/titan_controller/.id`
* **Windows:** `%APPDATA%\titan_controller\.id`

Ensure the `titan_controller` directory exists and contains your `.id` file before running the application. If the file is missing, the application will print the exact path where it expects to find it.

### 2. Optional Environment Variables
If your benchmark or Sniper paths differ from default locations (`~/Documents/sniperAFS/sniper` and `~/Vault/benchmarks/benchmarks`), you can override them via environment variables:

```bash
export SNIPER_ROOT="/path/to/your/sniper"
export BENCHMARK_ROOT="/path/to/your/benchmarks"
```

### 3. SSH Setup

The application communicates with Titan via SSH and SCP commands (`ssh titan` and `scp titan`).

Add the following block to your `~/.ssh/config` file:

```
Host titan
	Hostname bacchus.ugent.be
	User slurmslave
	Identityfile ~/.ssh/your-key-installed-on-titan
	IdentitiesOnly yes
```

Test your connection by running `ssh titan`. If you log in successfully (type `exit` to disconnect), your SSH configuration is complete.

## Compilation

Build the release binary using Cargo:

```bash
cargo build --release
```

The compiled binary will be placed at `./target/release/titan_controller`.

## Usage & Workflow

## 1. Preparing Templates

The application relies on two template JSON files located in ./script-template/:

- `experiment_template.json`: Defines the Sniper parameters required for the experiment and references a benchmark JSON file.

- `benchmark_template.json`: Lists the benchmarks to run along with their specific execution parameters.

## 2. Submitting Jobs

To test job submission, run:

```bash
cargo run -- --submit JOB --path ./script-template/experiment_template.json
```

This will generate an output tracking file at `host_destination_path` as defined in your experiment JSON file (by default: `/tmp/my_experiment`).

## 3. Collecting Jobs

Once the jobs complete on Titan (typically after several minutes to hours), collect the results:

```bash
cargo run -- --collect JOB --path /tmp/my_experiment
```

This command will:
- Automatically download completed job data.
- Run health checks and automatically resubmit failed jobs.
- Cache finished jobs locally to avoid re-running them in future experiments.

## Processing Results

A Python helper script is provided at `./process_scripts/process_scripts/process_sniper_experiment.py`.

Use the `get_saved(path, force_reread)` function to load the experiment data:

```Python
from process_scripts.process_sniper_experiment import get_saved
from pathlib import Path

# Path to the host_destination_path generated during submission
exp_path = Path("/tmp/my_experiment")

# Load cached version (or set force_reread=True to parse raw data again)
df = get_saved(exp_path, False)

# Print available experiment metadata and metrics
print(df.columns)
```

This returns a Pandas DataFrame containing all metadata and experiment metrics. You can filter and inspect columns based on your specific analysis requirements.

There are some python code to plot the data. But from personal experience, all plots are very different from each other. Trying to make something generic enough to plot the data you want. Resulted into simply wrapping Seaborn or Matplotlib into a new API. Which makes no real sense, so I suggest you make the plots on your case-by-case basis. (Although I would recommend you to use the Seaborn wrapper.)

# Development notes:

## Progress
This program handles everything with regards to experiments towards an HPC (for now only titan/slurm).
It can
- Create an submit an experiment with varying parameters and benchmarks
- Retrieve all the experiments, and retry them if needed
- List out on the server
    - all the running and past jobs
    - all the docker containers
    - all the traces on titan
- send data to the server
    - new docker containers
    - new traces
- It can delete
    - running jobs
    - docker containers
    - TODO: remove traces

## Features to add
- [ ] Download an individual job to inspect what was wrong.
- [ ] The cache database could become very big very quick. Especially, that it will not handle multi-programs. (It will be double overwrited.) Maybe in the future have one DB per git/bench/trace-hash, and use a lock-file to avoid this behaviour?

## Structure
The structure how the experiment is build works as followed
### Experiment
An Experiment contains a list of BenchmarkSuite.

### BenchmarkSuite
A BenchmarkSuite contains a list of SimulatorParameters.
A BenchmarkSuite assumes that it shares the same folder of benchmarks/traces to be mounted to the VM on the HPC. Which means that this can share the same types of mounts in the same JOB. So there will be only one JOB per BenchmarkSuite, as they all the tasks are sharing the same slurm-script. To differentiate tasks between each other, the JOB is run with a JOB-array, and so every task has its own task id.

### SimulatorParameter
A SimulatorParameter contains a list of BenchmarkParameters.
This is a set of parameters that needs to be passed to the Simulator (for now only Sniper). This is needed to (partially) create the task-script to run the simulator properly.

### BenchmarkParameter
A BenchmarkParameter contains a list of BenchmarkRuns.
This is a set of parameters that is specific for that benchmark. This can include special source-files or make commands, or also some special simulator-parameters specific to that benchmark.

### BenchmarkRun
Finally, this contains just a run index, such that the same benchmark could be run multiple times to eliminate variability if needed.

# Processing the data
A little python library is added here, where you can process the data into a pandas dataframe. It is not stable, nor well documented but is present if needed.

To install please run `pip3 install /path/to/process_scipts`, and those can be used in your python scripts as:
```python
from process_scripts import process_data
from process_scripts.graph_parameters import *
```
## Features to modify
For now all the data is collected into one giant dataframe with one datapoint per row. Ideally this changes to become one set of benchmarkRun (from the hierarchy above), and all the values are in the same row in different columns.
