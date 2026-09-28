use std::process::Command;
use std::path::Path;

pub fn send_command(command: &str) -> Result<(String, String), std::io::Error>{
    let output = Command::new("ssh")
        .args(["titan", command])
        .output()?;
    let stdout = String::from_utf8(output.stdout).unwrap();
    let stderr = String::from_utf8(output.stderr).unwrap();
    if stderr.len() > 0 {
        eprintln!("{:?} {:?}", stdout, stderr);
    }
    // ssh exiting non-zero (auth failure, network drop, remote command
    // failure, ...) must not look identical to "command ran, produced no
    // output" -- callers otherwise silently proceed on empty/partial data
    // (e.g. get_hash_titan() below returning an empty Vec instead of an
    // error), surfacing as a confusing panic far from the real cause
    // instead of a clean error naming the actual ssh failure.
    if !output.status.success() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("ssh command failed (exit {}): {}", output.status, stderr.trim()),
        ));
    }
    Ok((stdout, stderr))
}

pub fn send_files(src_path: &str, dst_path: &str) -> Result<(), std::io::Error> {
    let full_dst = format!("titan:{dst_path}");
    let output = Command::new("bash")
        .arg("-c")
        .arg(["scp", "-r", src_path, &full_dst].join(" "))
        .output()?;
    if output.stderr.len() > 0 {
        eprintln!("{:?} {:?}", String::from_utf8(output.stdout).unwrap(), String::from_utf8(output.stderr).unwrap());
        return Err(std::io::Error::new(std::io::ErrorKind::Other, "SCP sending files failed"));
    }
    Ok(())
}

pub fn get_files(src_path: &str, dst_path: &str) -> Result<(), std::io::Error> {
    let full_src = format!("titan:{src_path}");
    let output = Command::new("scp")
        .args(["-r", &full_src, dst_path])
        .output()?;
    if output.stderr.len() > 0 {
        eprintln!("{:?} {:?}", String::from_utf8(output.stdout).unwrap(), String::from_utf8(output.stderr).unwrap());
        return Err(std::io::Error::new(std::io::ErrorKind::Other, "SCP sending files failed"));
    }
    Ok(())
}

pub fn untar(src_path: &Path, dst_path: &Path, delete_tar: bool) -> Result<(), std::io::Error> {
    let output = Command::new("tar")
        .arg("-xzf")
        .arg(src_path)
        .arg("-C")
        .arg(dst_path)
        .output()?;
    if !output.status.success() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("tar extraction of {} failed (exit {}): {}", src_path.display(), output.status, String::from_utf8_lossy(&output.stderr).trim()),
        ));
    }
    if delete_tar {
        let _output2 = Command::new("rm").arg(src_path).output()?;
    }
    Ok(())
}

pub fn clean_dir(dir_path: &Path) -> Result<(), std::io::Error> {
    let _output = Command::new("bash")
        .arg("-c")
        .arg(["rm", "-r", dir_path.join("*").to_str().unwrap()].join(" "))
        .output()?;
    Ok(())
}

pub fn get_hash_titan(amount: usize) -> Result<Vec<String>, std::io::Error> {
    let cmd = format!("for i in $(seq 1 {}); do mktemp; done", amount);
    let (stdout, _) = send_command(&cmd)?;

    let mut hash_vec = Vec::<String>::new();
    let output_lines: Vec<&str> = stdout.split("\n").collect();
    for line in &output_lines {
        if ! line.contains("/tmp/tmp."){
            continue;
        }
        let line_split: Vec<&str> = line.split(".").collect();
        hash_vec.push(line_split[1].to_string());
    }
    
    let rm_cmd = format!("rm {}", output_lines.join(" "));
    send_command(&rm_cmd)?;

    return Ok(hash_vec);
}
