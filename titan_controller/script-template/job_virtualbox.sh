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

VM_original=<VM_NAME>
job_suffix=${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}

check_vm_exists() {
	vbox_list=`VBoxManage list vms`
	while [ $? -ne 0 ]; do
		vbox_list=`VBoxManage list vms`
	done
	var=`echo ${vbox_list} | grep -c "${VM_original}"`
	if [[ ${var} -eq "0" ]] ; then
		echo "VirtualBox to create clone from does not exist!" 1>&2
		wrap_up 0
	fi
}

check_and_wait_for_disk_space() {
	space_needed_in_byte=$(du -s ~/virtualbox/${VM_original} | cut -f 1)
	fails=0
	while [ $(( $space_needed_in_byte * 5 )) -ge $(df . --output="avail" | tail -n 1) ]; do
		echo "Not enough space left on disk, waiting" 1>&2
		sleep 600 #10mins
		((fails+=1))
		if [ $fails -ge 18 ]; then
			echo "Waited for 3h, still no disk space, aborting job" 1>&2
			wrap_up 0
		fi
	done

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

	scp results_${job_suffix}.tar.gz slurmslave@bacchus:results/.
	scp_output=$?

	if [ $scp_output -eq 0 ]; then
		if [ $results_dir_exists -eq 1 ]; then
	  		rm -r ${working_dir}
		else
			rm ~/results_${job_suffix}.tar.gz ~/stdout_${job_suffix}.txt ~/stderr_${job_suffix}.txt
		fi
	else
	  	echo "scp to control host failed!" 1>&2
	fi

	exit
}

copy_with_retry_from_bacchus() {
	submitted_file=$1
	rename_to=$2
	while true; do
		scp bacchus:$submitted_file $rename_to > /dev/null 2>&1
		if [ $? -eq 0 ]; then
			ssh bacchus "rm $submitted_file"
			break
		else
			sleep 1
		fi
	done
}

# ARGS: git-dir-path branch-name
checkout_git_repo() {
	cd ~/$1/master
	lockdir="gitlock"
	trap 'rm -rf "$lockdir"' SIGTERM #if job happens to be terminated by slurm
	while ! mkdir $lockdir 2>/dev/null; do
		echo "Cannot enter critical section now, waiting..."
		sleep 1m
	done
	while [ -f .git/index.lock ]; do #Should not be able to wait here...
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
		mkdir ../$GIT_ID
		git archive --format=tar --prefix=$GIT_ID/ HEAD | (cd .. && tar xf -)
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

#Arguments: mount_name host-path
mount_vbox() {
	echo "Mounting $1 on hostpath $2"
	VBoxManage sharedfolder add "${VM_name}" --name $1 --hostpath $2
	VBoxManage setextradata "${VM_name}" VBoxInternal2/SharedFoldersEnableSymlinksCreate/$1 1
}

shutdown_VM () {
	# stop VM
	echo "Stopping and cleaning up VM."
	VBoxManage controlvm "${VM_name}" poweroff
	
	# wait until the VM is not running anymore
	VM_running=""
	while [ -n "${VM_running}" ]; do
		VM_running=`VBoxManage list runningvms | grep "${VM_name}"`
		sleep 10
	done
}

HOSTNAME=`hostname`
echo "Running on machine ${HOSTNAME}."

check_vm_exists
check_and_wait_for_disk_space

# cd ${TMPDIR}
# echo "TMPDIR is ${TMPDIR}"

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
# copy_with_retry_from_bacchus ~/jobs/submitted/job_${job_suffix}.sh job.sh
cp $0 job.sh
copy_with_retry_from_bacchus ~/jobs/submitted/execute_${job_suffix}.sh execute.sh

# clone relevant VM and register
echo "Cloning and setting up VM."
VM_name="${VM_original}_${job_suffix}"
# VBoxManage clonevm "${VM_original}" --basefolder "${TMPDIR}" --name "${VM_name}" --register
VBoxManage clonevm "${VM_original}" --basefolder virtualbox --name "${VM_name}" --register
sleep 1 #wait a bit for the VM to properly register

# modify the number of cores the VM has access to
VBoxManage modifyvm "${VM_name}" --cpus <CORES>
# modify the memory the VM has access to
VBoxManage modifyvm "${VM_name}" --memory <MEMORY>
# add directory as the output directory for the VM and source dir for the execute.sh script
mount_vbox run_mount ${working_dir}
<Virtualbox_MOUNTS>

# start vm, this automatically starts the execution of execute.sh
echo "Starting VM."
VBoxManage startvm "${VM_name}" --type headless

# wait loop for job
waiting_started=0
tries=0
while [ ! -f ${working_dir}/finished ]; do
	if [ ${waiting_started} -gt 600 ] ; then #waiting for 10mins for VM to start. If not started by then, the VM did not start properly
		if [ ! -f ${working_dir}/started ] ; then
			echo "VirtualBox did not start properly, trying again"
			shutdown_VM
			
			if [ ${tries} -ge 3 ] ; then
				echo "3 tries failed, abandoning"
				break
			fi

			VBoxManage startvm "${VM_name}" --type headless
			waiting_started=0
			((tries+=1))
			
		fi
	fi
	sleep 10
	((waiting_started+=10))
done

shutdown_VM

# delete VM
VBoxManage unregistervm --delete "${VM_name}"

# archive the results and copy them to the control node
wrap_up 1
