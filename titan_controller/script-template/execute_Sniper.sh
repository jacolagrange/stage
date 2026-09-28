#!/bin/bash
readonly BENCHMARKS_DIR="/mnt/benchmarks"
readonly RUN_DIR="/mnt/run"
readonly SNIPER_DIR="/mnt/sniper"
readonly INPUT_DIR="/mnt/input"
readonly TRACES_DIR="/mnt/traces"

export SNIPER_ROOT=${SNIPER_DIR}
export GRAPHITE_ROOT=${SNIPER_DIR}
export BENCHMARKS_ROOT=${BENCHMARKS_DIR}

export OMP_WAIT_POLICY=active

lock() {
    local LOCKFILE_DIR=$(pwd)
    local BUILD_LOCK=${LOCKFILE_DIR}/make.lock

    if [[ $1 == "x" ]]; then
      echo "$(date +'%H:%M:%S %d-%m-%Y') -- Attempting to lock the directory ${LOCKFILE_DIR}"
    fi

    if [[ $1 == "x" ]]; then
      # mkdir is atomic, it seems creating a file with touch is not necessarily
      until mkdir ${BUILD_LOCK} 2> /dev/null; do
        echo "${BUILD_LOCK} exists, another simulation is building the Sniper branch."
        sleep 10
      done
      echo "$(date +'%H:%M:%S %d-%m-%Y') -- Acquired lock on ${LOCKFILE_DIR}"
    else
      rm -r ${BUILD_LOCK}
      echo "$(date +'%H:%M:%S %d-%m-%Y') -- Unlocked directory ${LOCKFILE_DIR}"
    fi
    return 0
}

error_exit() {
    local ERROR_STRING="$@"

    echo "$(date +'%H:%M:%S %d-%m-%Y') -- ERROR -- $ERROR_STRING"
    exit 1
}

#INPUT (BUILD_DIR) (MAKE_CMD) (OUTPUT_FILE) (ERROR_FILE)
build() {
    if [ ! -f $1/.built ]; then
      echo "Building $1 branch. "
      cd $1
    
      gcc --version
      python3 --version
    
      lock x || error_exit "Could not lock directory before make operation!"
    
      $2 > ${RUN_DIR}/$3 2> ${RUN_DIR}/$4
      if [ $? -eq 0 ]; then
        # if the make operation succeeded, mark this folder as built
        touch .built
      fi

      chown -R slurmslave:slurmslave $1
    
      lock u || error_exit "Could not unlock directory after make operation!"
    else
      echo "Sniper branch was built previously!"
    fi
}

# start
date > ${RUN_DIR}/started

build ${SNIPER_DIR} "make" make_sniper.out make_sniper.err

# If something in Benchmark directory, build it.
if [ -n "$(ls -A ${BENCHMARKS_DIR} 2>/dev/null)" ]; then
    build ${BENCHMARKS_DIR}/<BENCH_BUILD_DIR> <BUILD_COMMAND> make_benchmarks.out make_benchmarks.err
fi

# run simulation
cd ${RUN_DIR}
# MODIFY SNIPER COMMAND LINE HERE
<SETUP_CMD>
${SNIPER_DIR}/run-sniper -d ${RUN_DIR} <ARGUMENTS>
${SNIPER_DIR}/tools/mcpat.py -d ${RUN_DIR}

chown -R slurmslave:slurmslave .

# finish
date > ${RUN_DIR}/finished
