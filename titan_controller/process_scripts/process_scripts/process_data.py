import re
import math
import numpy as np
import pandas
import json
import sqlite3
import pathlib
import configparser
import subprocess
import importlib.util

from typing import List, Dict, Tuple

SNIPER_PATH="/home/jaime/Documents/sniperAFS/sniper"

# ------------------------------- SET UP FOR ALL SQL RELATED METHODS -------------------------------------

# Open up sqlite3 connection with a path to the db_file
# Once connection is established, return a connection
def create_connection(db_file: pathlib.Path) -> sqlite3.Connection:
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("select * from `names`")
        c.close()
    except sqlite3.Error as e:
        fd = open("fails.txt", "a+")
        fd.write(f"{db_file.parent}\n")
        fd.close()
        print("error at", db_file, e)
        conn = None
    
    return conn

# Reads the names available in the database, (usually comes in tuples: (objectname, metricname))
# returns list of all the names containting the tuples of strings
def read_metricnames(conn) -> Dict[str, Tuple[str, str]]:
    names = {}
    c = conn.cursor()
    try:
        c.execute('select nameid, objectname, metricname from `names`')
        for nameid, objectname, metricname in c.fetchall():
            names[nameid] = (objectname, metricname)
    except sqlite3.Error as e:
        print("error at reading names")
    c.close()
    return names

def get_metric_values_perf_model(conn: sqlite3.Connection, desired_metrics: List[Dict[str, object]]) -> List[Tuple[int, int, str, str, int]]:
    ''' Get values of model, with some timings

    Parameter
    ---------
    conn: An SQLite3 connection to the DB
    Desired_metric : Tuple[str, str] if empty list then all metrics are taken
        e.g. ("perforamnce_model", "elapsed_time") or ("L1-D", "load-misses")
    '''
    cur = conn.cursor()

    namefilter = ""
    if desired_metrics is not None and len(desired_metrics) > 0: # no need to read all those names
        names = read_metricnames(conn)
        # print(names)

        expanded_list = [metric["metric"] for metric in desired_metrics if metric["where"] == "stats"]
        metric_ids = [str(nameid) for nameid, (objectname, metricname) in names.items() if (objectname, metricname) in expanded_list]
        namefilter = ' and nameid in (%s)' % ','.join(metric_ids)
    
    # print(names, namefilter)

    #cur.execute("SELECT prefixid FROM `prefixes` WHERE prefixname = 'stop'")
    #prefixid = [2, 3] (cur.fetchall())[0][0]       We know that 2 = ROI-BEGIN & 3 = ROI-END (1 = START, 4 = STOP)

    cur.execute("SELECT * FROM (SELECT * FROM `values` WHERE prefixid=3 %s) AS T1 LEFT JOIN (SELECT * FROM `values` WHERE prefixid=2 %s) AS T2 on T1.nameid = T2.nameid AND T1.core = T2.core LEFT JOIN `names` on T1.nameid = `names`.nameid" % (namefilter, namefilter))

    #cur.execute("SELECT * FROM `values` WHERE prefixid = ? %s;" % namefilter, (prefixid[0],)) #get ROI_BEGIN

    rows = cur.fetchall()

    values = []
    # row: prefixid (event), nameid, core, value1, prefixid, objectid, core, value2, nameid, obj_name, metric_name
    for row in rows:
        values.append((row[2], row[1], row[9], row[10], row[3] - (row[7] if row[7] else 0) )) #if no ROI-BEGIN
    cur.close()

    return values

# ---------------------------------------------- Config related method ---------------------------------------------------

def get_config_values(cfg_file: pathlib.Path, desired_metrics: List[Dict[str, object]]) -> List[Tuple[int, int, str, str, float]]:
    config = configparser.ConfigParser()
    config.read(cfg_file)

    values = []
    idx = 0
    if desired_metrics is not None and len(desired_metrics) > 0:
        for metric in desired_metrics:
            if metric["where"] != "config":
                continue
            metric_list = metric["metric"].split("/")
            metric_path = "/".join(metric_list[:-1])
            metric_key = metric_list[-1]
            values.append((0, 2000 + idx, metric_path, metric_key, config[metric_path][metric_key]))
            idx += 1
    else:
        for section in config.sections():
            for key, value in config.items(section):
                values.append((0, 2000 + idx, section, key, value))
                idx += 1

    return values


# ---------------------------------------------- Get McPat numbers ---------------------------------------------------
def power_stack(power_dat, powertype = 'total', nocollapse = False):
  def getpower(powers, key = None):
    def getcomponent(suffix):
      if key: return powers.get(key+'/'+suffix, 0)
      else: return powers.get(suffix, 0)
    if powertype == 'dynamic':
      return getcomponent('Runtime Dynamic')
    elif powertype == 'static':
      return getcomponent('Subthreshold Leakage with power gating') + getcomponent('Gate Leakage')
    elif powertype == 'total':
      return getcomponent('Runtime Dynamic') + getcomponent('Subthreshold Leakage with power gating') + getcomponent('Gate Leakage')
    elif powertype == 'peak':
      return getcomponent('Peak Dynamic') + getcomponent('Subthreshold Leakage with power gating') + getcomponent('Gate Leakage')
    elif powertype == 'peakdynamic':
      return getcomponent('Peak Dynamic')
    elif powertype == 'area':
      return getcomponent('Area') + getcomponent('Area Overhead')
    else:
      raise ValueError('Unknown powertype %s' % powertype)
  data = {
    'l2':               sum([ getpower(cache) for cache in power_dat.get('L2', []) ])  # shared L2
                        + sum([ getpower(core, 'L2') for core in power_dat['Core'] ]), # private L2
    'l3':               sum([ getpower(cache) for cache in power_dat.get('L3', []) ]),
    'nuca':             sum([ getpower(cache) for cache in power_dat.get('NUCA', []) ]),
    'noc':              getpower(power_dat['Processor'], 'Total NoCs'),
    'dram':             getpower(power_dat['DRAM']),
    'core':             sum([ getpower(core, 'Execution Unit/Instruction Scheduler')
                              + getpower(core, 'Execution Unit/Register Files')
                              + getpower(core, 'Execution Unit/Results Broadcast Bus')
                              + getpower(core, 'Renaming Unit')
                              for core in power_dat['Core']
                            ]),
    'core-ifetch':      sum([ getpower(core, 'Instruction Fetch Unit/Branch Predictor')
                              + getpower(core, 'Instruction Fetch Unit/Branch Target Buffer')
                              + getpower(core, 'Instruction Fetch Unit/Instruction Buffer')
                              + getpower(core, 'Instruction Fetch Unit/Instruction Decoder')
                              for core in power_dat['Core']
                            ]),
    'icache':           sum([ getpower(core, 'Instruction Fetch Unit/Instruction Cache') for core in power_dat['Core'] ]),
    'dcache':           sum([ getpower(core, 'Load Store Unit/Data Cache') for core in power_dat['Core'] ]),
    'core-alu-complex': sum([ getpower(core, 'Execution Unit/Complex ALUs') for core in power_dat['Core'] ]),
    'core-alu-fp':      sum([ getpower(core, 'Execution Unit/Floating Point Units') for core in power_dat['Core'] ]),
    'core-alu-int':     sum([ getpower(core, 'Execution Unit/Integer ALUs') for core in power_dat['Core'] ]),
    'core-mem':         sum([ getpower(core, 'Load Store Unit/LoadQ')
                              + getpower(core, 'Load Store Unit/StoreQ')
                              + getpower(core, 'Memory Management Unit')
                              for core in power_dat['Core']
                            ]),
  }
  data['core-other'] = getpower(power_dat['Processor']) - (sum(data.values()) - data['dram'])
  return data
  #return buildstack.merge_items({ 0: data }, all_items, nocollapse = nocollapse)


McPat_PATH = f"{SNIPER_PATH}/tools/mcpat.py"
def get_McPat(experience_path: pathlib.Path, desired_metrics: List[Dict[str, object]], time_in_seconds: float):
    if desired_metrics is not None and len(desired_metrics) > 0 and not any([metric["where"] == "McPat" for metric in desired_metrics]):
        return []

    cmd = [McPat_PATH, "-d", experience_path, "--no-graph", "--no-text"]
    mcpat_output_file = experience_path / "power.py"
    if(not mcpat_output_file.exists()):
        subprocess.run(cmd, cwd=experience_path)

    spec = importlib.util.spec_from_file_location("power", mcpat_output_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    power_values = power_stack(mod.power, "total")
    energy_values = {k: float(v) * time_in_seconds for k,v in power_values.items()}
    area_values = power_stack(mod.power, "area")

    values = []
    idx = 0
    if desired_metrics is not None and len(desired_metrics) > 0:
        for metric in desired_metrics:
            if metric["where"] != "McPat":
                continue
            metric_to_use = metric["metric"][1]
            dict_use = power_values
            if metric_to_use == "Power":
                dict_use = power_values
            elif metric_to_use == "Energy":
                dict_use = energy_values
            elif metric_to_use == "Area":
                dict_use = area_values

            for k, v in dict_use.items():
                values.append((0, 4000 + idx, metric_to_use, k, v))
                idx += 1

    else:
        for (metric_to_use, dict_use) in [("Power", power_values), ("Energy", energy_values), ("Area", area_values)]:
            for k, v in dict_use.items():
                values.append((0, 4000 + idx, metric_to_use, k, v))
                idx += 1
            
    return values


CPI_SCRIPT_PATH = f"{SNIPER_PATH}/tools/cpistack.py"
def get_CPI_stack(experience_path: pathlib.Path, desired_metrics: List[Dict[str, object]]):
    if desired_metrics is not None and len(desired_metrics) > 0 and not any([metric["where"] == "CPI-stack" for metric in desired_metrics]):
        return []

    cmd = [CPI_SCRIPT_PATH, "-d", str(experience_path), "|", "tr", "-s", "\" \""]
    cpi_output_file = experience_path / "cpi-stack.txt"
    if(not cpi_output_file.exists()):
        f = open(cpi_output_file, "w")
        subprocess.run(cmd, cwd=experience_path, shell=True, stdout=f)
        f.close()

    values = []
    idx = 0
    found_first_line = False
    with cpi_output_file.open() as f:
        for line in f.readlines():
            if "CPI" in line:
                found_first_line = True
                continue
            if not found_first_line:
                continue

            tmp = line.split()
            if(len(tmp) == 0):
                continue

            element, value, time = tmp
            values.append((0, 6000 + idx, "CPI", element, value))
            idx += 1

    return values

        

# ---------------------------------------------------- GET PATHS ---------------------------------------------------------

def get_all_paths_per_experiment(exp_dir_name : str) -> List[pathlib.Path]:
    raw_data_path = pathlib.Path(exp_dir_name)
    experiments = raw_data_path.glob("*")
    exp_dict = []
    for experiment in experiments:
        if(experiment.is_dir()):
            exp_dict.append(experiment)
    return exp_dict

# ---------------------------------------------------- All read simulated time (txt) realted methods -------------------------------

reg_sim_time = re.compile("\[SNIPER\] Elapsed time: [0-9]*\.[0-9]* seconds")
def read_sim_time(output_file: pathlib.Path) -> float:
    if output_file is None: return 0
    global reg_sim_time
    elapsed_time = 0
    file_descriptor = open(output_file, "r")
    lines = str(file_descriptor.readlines())
    file_descriptor.close()
    str_elapsed_time = str(reg_sim_time.search(lines).group(0)).split()[3]
    elapsed_time = float(str_elapsed_time)
    return elapsed_time

#----------------------------------- Hulp functions (intermediate steps of get_values) -------------------------------------------

def _get_raw_values_of_version(exp_path: pathlib.Path, metrics, extra_files: List[Tuple[str, str, str]]) -> pandas.DataFrame:
    exp_args = read_args(exp_path)
    exp_columns = ["benchmark", "nameid", "objectname", "metricname", "exp_nr"] + list(exp_args.keys()) + ["core_id", "value"]
    benchmarks_dir = exp_path.glob("*")
   
    df_exp = pandas.DataFrame(columns = exp_columns)
    experiment_name = exp_path.stem
    
    # here go over all the number of experiments repeated
    for benchmark_dir in benchmarks_dir:
        if not benchmark_dir.is_dir():
            continue
        benchmark_name = benchmark_dir.stem + ".".join(benchmark_dir.suffixes)

        for exp_nr in benchmark_dir.glob("*"):
            if not exp_nr.is_dir():
                continue
            print(f"processing {experiment_name}-{benchmark_name} nr {exp_nr.stem}")
            # get the values of the experiments, filtered by metrics, or not...
            try:
                conn = create_connection(exp_nr.joinpath("sim.stats.sqlite3"))
                if conn == None:
                    continue
                temp = get_metric_values_perf_model(conn, metrics)
                conn.close()
        
                # get the simulation time value
                sim_time_path_file = None
                titan_out = exp_nr.joinpath("stdout_vm.txt")
                local_out = exp_nr.joinpath("output.txt")
                if titan_out.exists():
                    sim_time_path_file = titan_out
                if local_out.exists():
                    sim_time_path_file = local_out
                sim_time = read_sim_time(sim_time_path_file)#before "output.txt" for 
                temp.append((0, 0, "sim_time", None, sim_time))

                # get the config time values
                config_values = get_config_values(exp_nr.joinpath("sim.cfg"), metrics)
                temp += config_values

                # Get the McPat value
                seconds = 0.0
                for val in temp:
                    if(val[2] == "performance_model" and val[3] == "elapsed_time"):
                        seconds = val[4] / 1e15
                power_values = get_McPat(exp_nr, metrics, seconds)
                temp += power_values

                # Get the CPI stack values
                cpi_stack_values = get_CPI_stack(exp_nr, metrics)
                temp += cpi_stack_values

                # Add the values of the extra_files
                for idx, (extra_file_name, regex_string, column_name) in enumerate(extra_files):
                    results = find_value_in_file(exp_nr.joinpath(extra_file_name), regex_string)
                    if(len(results) == 1):
                        temp.append((0, 1000 + idx * 10, column_name, None, results[0])) 
                    elif(len(results) > 1):
                        for res_idx, res in enumerate(results):
                            temp.append((0, 100000 + idx * 1000 + res_idx, column_name + str(res_idx), None, res)) 

                
        
                # Base structure of the dataframe will be: core_id, nameid, objectname, metricname & value, like it is found in the sqlite3 database
                df_tmp = pandas.DataFrame(temp, columns=["core_id", "nameid", "objectname", "metricname", "value"])
                # Appending exp_nr, which will be removed later with aggregation. And also adding the experiment name.
                df_tmp.loc[:, "exp_nr"] = exp_nr.stem
                df_tmp.loc[:, "benchmark"] = benchmark_name
                # Lastly adding all the meta-experiment-values (values about the experiment, which were passed as arguements when running) 
        
                try:
                    df_exp = pandas.concat([df_exp, df_tmp], axis=0, ignore_index=True)
                except Exception as e:
                    import traceback
                    print(f"Issue handeling at {exp_path} nr {exp_nr.stem}, ignoring and continue")
                    print(f"df_exp:\n{df_exp}")
                    print(f"df_tmp:\n{df_tmp}")
                    print(e)
                    traceback.print_exc()
                    exit(0)
               
            except Exception as e:
                import traceback
                print(f"Issue handeling {experiment_name} nr {exp_nr.stem}, ignoring and continue")
                print(e)
                traceback.print_exc()
                exit(0)
        
    
    #Adding the experiment arguments to the data
    for name, vals in exp_args.items():
        df_exp.loc[:, name] = vals

    return df_exp



#----------------------------------- Get all values wanted from avg_dev -----------------------------------------------------------

def get_values(dirpath: pathlib.Path, metrics: List[Dict[Tuple[str, str],List[int]]],extra_files: List[Tuple[str, str, str]]):
    experiments = get_all_paths_per_experiment(dirpath)

    # creating the dataframe containing everything
    df = pandas.DataFrame()


    # go over all the files (here per experiment variation)
    for exp_path in experiments:
        df_exp = _get_raw_values_of_version(exp_path, metrics, extra_files) 
        df_exp["value"] = pandas.to_numeric(df_exp["value"], errors="coerce") #This will change all pure-string values to NAN

        # Take average accross experiment numbers
        # Once all experients of that version is processed in df_exp. Will calculate first, the mean per each simulation core count.
        all_columns = df_exp.columns
        all_columns = all_columns.drop("exp_nr")

        df_exp_mean = df_exp.groupby(all_columns.to_list(), as_index=False).agg({"value": ["mean", "std"]})
        df_exp_mean.columns = [col1 if col1 != "value" else col2 for (col1, col2) in df_exp_mean.columns.to_list()]

        # Then calcuate the simulation accross all cores. (core_id == -1) 
        all_columns = all_columns.drop("core_id")
        df_exp_mean_core_ids = df_exp.groupby(all_columns.to_list(), as_index = False).agg({"value": ["mean", "std"]})
        df_exp_mean_core_ids.columns = [col1 if col1 != "value" else col2 for (col1, col2) in df_exp_mean_core_ids.columns.to_list()]

        df_exp_mean_core_ids.loc[:, "core_id"] = -1

        df_exp_mean = pandas.concat([df_exp_mean, df_exp_mean_core_ids], ignore_index=True)
        df = pandas.concat([df, df_exp_mean], ignore_index=True)

    return df 

 

def get_values_pandas(dirpath: str, metrics: List[Dict[Tuple[str, str], List[int]]], force_remake: bool = False, extra_files: List[Tuple[str, str, str]] = []) -> pandas.DataFrame:
    '''
    Parameters
    ----------
    dirpath: Where are the experiments located in the sturcture: experiments_variation -> 0-15 -> actual experiments files
    metrics: Which metrics should be looked at, if empty get all metrics
    '''
    dirpath = pathlib.Path(dirpath)
    df_save = dirpath.joinpath("df_backup.pickle.xz")
    df = retrieve_dataframe(df_save)
    if df.empty or force_remake:
        df = get_values(dirpath, metrics, extra_files)
        df.to_pickle(df_save, compression = "infer")
   
    return df
        
# ------------------------------------- helping functions --------------------------------------------------

def retrieve_dataframe(df_save: pathlib.Path) -> pandas.DataFrame:
    df = pandas.DataFrame()
    if df_save.exists():
        df = pandas.read_pickle(df_save, compression="infer")
    return df


def extract_experiments_args(name: str) -> List[int]:
    cmd_index = name.find("_cmd")
    snp_index = name.find("_snpargs")

    cmd_args_str = name[cmd_index + len("_cmd"):snp_index].split("-") if cmd_index + len("_cmd") < snp_index else []
    snp_args_str = name[snp_index + len("_snpargs"):len(name)].split("-") if snp_index + len("_snpargs") < len(name) else []

    cmd_args = [int(val) if val.isdigit() else str(val) for val in cmd_args_str]
    snp_args = [int(val) if val.isdigit() else str(val) for val in snp_args_str]

    return_args = []
    if cmd_args != [None]:
        return_args += cmd_args
    if snp_args != [None]:
        return_args += snp_args

    return return_args

def find_value_in_file(file_path: pathlib.Path, regex_string: str) -> List[str]:
    reg_pattern = re.compile(regex_string)
    fd = open(file_path)
    all_values = []
    for line in fd:
        for res in reg_pattern.finditer(line):
            all_values.append(res.group())
    return all_values
        # if search_string in line:
        #     res = float(line[line.find(search_string) + len(search_string):])
        #     return res

def make_ipc(df: pandas.DataFrame) -> pandas.DataFrame:
    columns = list(df.columns)
    columns.remove("std")
    ret_df = df[(df["metricname"] == "frequency") | (df["metricname"] == "instruction_count") | (df["metricname"] == "elapsed_time")]

    keep_columns = columns
    keep_columns.remove("nameid")
    keep_columns.remove("objectname")
    keep_columns.remove("metricname")
    keep_columns.remove("mean")
    new_columns = ["objectname", "metricname"]

    ret_df = ret_df.pivot_table(index=keep_columns, columns=new_columns, values="mean")
    ret_df.reset_index(inplace=True)
    ret_df["IPC"] = ret_df["performance_model"]["instruction_count"] / ret_df["performance_model"]["elapsed_time"] / ret_df["perf_model/core"]["frequency"] * 1000000 #because fs * Ghz, there is a 1000000 factor still left

    ret_df = ret_df[keep_columns + ["IPC"]]
    ret_df.columns = ret_df.columns.droplevel(1)
    ret_df = ret_df.rename(columns={"IPC": "mean"})
    ret_df["objectname"] = "IPC"
    ret_df["metricname"] = "IPC"

    #deprecated: ret_df = ret_df.append(df, ignore_index=True)
    ret_df = pandas.concat([ret_df, df], ignore_index=True)

    return ret_df

def read_args(path: str) -> object:
    read_p = pathlib.Path(path) / "args.json"
    dict_args = {}
    if not read_p.exists():
        print(f"WARNING args.json at path {read_p} does not exist, proceeding without")
        exit(1)
        return None
    with open(read_p, "r") as json_file:
        try:
            dict_args = json.load(json_file)
        except Exception as e:
            import traceback
            print(f"Something went wrong reading json_file {read_p}")
            print(e)
            traceback.print_exc()
            exit(1)
        json_file.close()

    return dict_args

def get_experiment_baseline(json_path: str) -> dict:
    experiment_path = pathlib.Path(json_path)
    baseline = {}
    if not experiment_path.exists():
        return baseline
    with open(experiment_path, "r") as json_file:
        data = json.load(json_file)
        for param_name, param_values in data["sniper_parameters"]["parameters"][0]["values"].items():
            baseline[param_name] = param_values[0]
    return baseline
