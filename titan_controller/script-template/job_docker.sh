#!/bin/bash

#SBATCH --account=<ACCOUNT>
#SBATCH --job-name=<JOB>
#SBATCH --qos=batch_qos
#SBATCH --partition=batch
#SBATCH --cpus-per-task=<CORES>
#SBATCH --output=stdout_%A_%a.txt
#SBATCH --error=stderr_%A_%a.txt
#SBATCH --array=1-<TASKS>
# #SBATCH --tmp=15G #need to update the slurm.conf to be able to reserve some TmpDisk space

readonly sniper_mount="/mnt/sniper"
readonly benchmarks_mount="/mnt/benchmarks"
readonly input_mount="/mnt/input"
readonly traces_mount="/mnt/traces"

original_image=<VM_NAME>
job_suffix=${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}

check_image_exists() {
	vbox_list=`docker image ls`
	while [ $? -ne 0 ]; do
		vbox_list=`docker image ls`
	done
	var=`echo ${vbox_list} | grep -c "${original_image}"`
	if [[ ${var} -eq "0" ]] ; then
		echo "Docker image does not exist!" 1>&2
		wrap_up 0
	fi
}

wrap_up() {
	echo "Copying results."
	results_dir_exists=$1
	if [ $results_dir_exists -eq 1 ]; then
		mv ~/stdout_${job_suffix}.txt .
		mv ~/stderr_${job_suffix}.txt .

		tar czf results_${job_suffix}.tar.gz *
		
	else
		tar czf results_${job_suffix}.tar.gz ~/stdout_${job_suffix}.txt ~/stderr_${job_suffix}.txt
	fi

	copy_with_retry_bacchus to results_${job_suffix}.tar.gz results/.

	if [ $? -eq 0 ]; then
		if [ $results_dir_exists -eq 1 ]; then
	  		rm -r ${working_dir}
		else
			rm ~/results_${job_suffix}.tar.gz ~/stdout_${job_suffix}.txt ~/stderr_${job_suffix}.txt
		fi
	fi

	exit
}

copy_with_retry_bacchus() {
	direction=$1 #"to" or "from"
	source_file=$2
	target_file=$3
	max_retries=10
	attempt=0

	while true; do
		if [ "$direction" = "from" ]; then
			scp bacchus:"$source_file" "$target_file" > /dev/null 2>&1
		elif [ "$direction" = "to" ]; then
			scp "$source_file" bacchus:"$target_file" > /dev/null 2>&1
		else
			echo "Invalid direction: must be 'to' or 'from'" >&2
			return 2
		fi

		if [ $? -eq 0 ]; then
			if [ "$direction" = "from" ]; then
				ssh bacchus "rm '$source_file'" > /dev/null 2>&1
			fi
			break
		else
			attempt=$((attempt + 1))
			if [ $attempt -ge $max_retries ]; then
				echo "Failed to copy file $direction bacchus after $max_retries attempts." >&2
				return 1
			fi
			sleep_time=$((2 ** (attempt -1)))
			sleep $sleep_time
		fi
	done
}

# ARGS: git-dir-path branch-name
checkout_git_repo() {
	cd ~/$1/master
	lockdir="gitlock"
	trap 'rm -rf "$lockdir"' SIGTERM #if job happens to be terminated by slurm
	while ! mkdir $lockdir 2>/dev/null; do
		echo "Cannot enter critical section now in ~/$i/master, waiting..."
		sleep 1m
	done
	while [ -f .git/index.lock ]; do #Should not be able to wait here...
		echo "Stuck behind .git/index.lock in ~/$i/master, waiting..."
		sleep 1m
	done

	git pull
	git checkout $2
	# check if branch-name argument was a git id or an actual branch
	DETACHED=`git branch | grep -c detached`
	GIT_ID=`git rev-parse HEAD`
	declare -g ${1^^}_GIT_ID=$GIT_ID
	echo "Branch git-id is $GIT_ID."
	if [ ! -d ../$GIT_ID ]; then
		# make a directory with the git id as name and copy the branch source code there
		cp -r ../master ../$GIT_ID
		echo `date +"%d-%m-%y"` > ../$GIT_ID/.last_used.txt
	else
		echo `date +"%d-%m-%y"` > ../$GIT_ID/.last_used.txt
	fi

	if [ $2 != "master" ]; then
		git checkout master
		if [ $DETACHED -eq 0 ]; then
			git branch -D $2
		fi
	fi

	rm -r "$lockdir"
	trap - SIGTERM #finished here. (not ideal, could still have some issues, but good enough?)
	cd ~
}

HOSTNAME=`hostname`
echo "Running on machine ${HOSTNAME}."

check_image_exists

# checkout (multiple) git repositories if necessary
# eg.: checkout_git_repo ~/sniper/master my-branch
<GIT-REPOSITORIES>

# setup a temporary directory
job_dir="running/job_${job_suffix}"
echo "Creating temporary directory ${job_dir}."
mkdir -p ${job_dir}
if [ $? -ne 0 ]; then
	echo "results job dir failed" 1>&2
	wrap_up 0
fi
cd ${job_dir}
if [ $? -ne 0 ]; then
	echo "could not cd into results dir" 1>&2
	wrap_up 0
fi
# get absolute path
working_dir=`pwd`

# copy the job and execute script from the command node, implement while sleep to make sure they are already copied
echo "Copying execution files."
cp $0 job.sh
copy_with_retry_bacchus from ~/jobs/submitted/execute_${job_suffix}.sh execute.sh
if [ $? -ne 0 ]; then
	echo "Could not copy execute script from bacchus" >&2
	wrap_up 1
fi
chmod +x execute.sh

container_name="${original_image}_${job_suffix}"
echo "Starting container."
#To allow sniper to pull all the submodules, copy files etc. Mount /mnt/perflab as read only
docker run --name ${container_name} \
	--rm \
	--user root \
	-v ${working_dir}:/mnt/run \
	-w /mnt/run \
	-v /mnt/perflab:/mnt/perflab:ro \
	<DOCKER_MOUNTS>--cpus=<CORES> \
	--memory=<MEMORY>m \
	${original_image} /mnt/run/execute.sh > "${working_dir}/stdout_vm.txt" 2> "${working_dir}/stderr_vm.txt"

# archive the results and copy them to the control node
wrap_up 1
