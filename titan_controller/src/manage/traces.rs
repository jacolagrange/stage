use crate::communication::ssh;
use std::path::Path;

pub fn list_traces() -> Result<(), std::io::Error> {
    let (stdout, stderr) = ssh::send_command("tree -d /home/slurmslave/traces")?;
    if stderr.len() > 0 {println!("{}", stderr);}
    else {println!("{}", stdout);}
    Ok(())
}

//TODO update this function to do the distribution ourselves.
pub fn upload_trace(trace_path: &Path) -> Result<(), std::io::Error> {
    if ! trace_path.is_dir() {
        println!("The traces should be contained within a folder to upload at once.");
        return Ok(());
    }
    let destination = Path::new("/home/slurmaslave/traces/sift");
    println!("Uploading trace to bacchus");
    let _ = ssh::send_files(trace_path.to_str().unwrap(), destination.to_str().unwrap());
    println!("Distributing to other nodes");
    let command = format!("/home/slurmadmin/scripts/distribute_VM.py {}/{}", destination.to_str().unwrap(), trace_path.file_name().unwrap().to_str().unwrap());
    ssh::send_command(&command)?;
    Ok(())
}
