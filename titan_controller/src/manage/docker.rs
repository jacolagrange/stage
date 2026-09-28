use std::path::Path;

use crate::communication::ssh;
use super::status;

pub fn list_dockerfiles() -> Result<(), std::io::Error> {
    let (stdout, stderr) = ssh::send_command("ls -l /home/slurmslave/Dockefiles")?;
    if stderr.len() > 0 {println!("{}", stderr);}
    else {println!("{}", stdout);}
    Ok(())
}

//TODO check if this replaces the old dockerfile? -> I think it overwrites it
pub fn upload_dockerfile(dockerfile_path: &Path) -> Result<(), std::io::Error> {
    let destination = Path::new("/home/slurmslave/Dockerfiles/");
    println!("Uploading Dockerfile to bacchus");
    let _ = ssh::send_files(dockerfile_path.to_str().unwrap(), destination.to_str().unwrap());
    let mut container_name = String::from(dockerfile_path.file_stem().unwrap().to_str().unwrap());
    container_name = container_name.to_lowercase().replace("dockerfile", "").trim_matches('_').to_string().trim_matches('-').to_string();
    if !container_name.is_empty() {
        println!("Distributing {container_name} to other nodes");
        let remote_file_path = destination.join(dockerfile_path.file_name().unwrap());
        let command = format!("for node in {{01..16}}; do scp {remote_file_path_str} titan${{node}}:{remote_file_path_str}; ssh titan${{node}} 'docker build --no-cache -t {container_name} -f {remote_file_path_str} {destination_str}' & done", remote_file_path_str = remote_file_path.to_str().unwrap(), destination_str = destination.to_str().unwrap());
        ssh::send_command(&command)?;
    } else {
        eprintln!("Error the container name is empty, please name you file such as dockerfile_your_name!");
    }
    Ok(())
}

//TODO test this function
pub fn delete_dockerfile(dockerfile_name: &str) -> Result<(), std::io::Error> {
    println!("Deleting Docker iamge {} from bacchus and all titan nodes", dockerfile_name);
    println!("This cannot be undone, are you sure you want to continue? y|N");
    let mut answ = String::new();
    std::io::stdin().read_line(&mut answ).unwrap();
    if answ.trim().to_lowercase().contains("y"){
        println!("Proceeding");
        let active_nodes = status::get_active_nodes()?;
        ssh::send_command(format!("rm /home/slurmslave/virtualbox/{}", dockerfile_name).as_str())?;
        let unregister_cmd = format!("docker ps -a --filter \"ancestor={dockerfile_name}\" -q | xargs -r docker rm -f; docker rmi -f {dockerfile_name}");
        for node_nr in active_nodes {
            let command = format!("ssh titan{:02} {}", node_nr, unregister_cmd);
            println!("{}", command);
            ssh::send_command(command.as_str())?;
        }
    } else {
        println!("deletion aborted");
    }
    Ok(())
}


