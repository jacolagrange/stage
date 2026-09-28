use crate::communication::ssh;

use std::vec::Vec;
use std::str::FromStr;

pub fn get_active_nodes() -> Result<Vec<usize>, std::io::Error> {
    let mut nodes = vec!();
    let (stdout, _) = ssh::send_command("sinfo -o \"%N %a\" | grep up")?;
    let range_titan = stdout.split_whitespace().next().unwrap();
    if let (Some(start_idx), Some(end_idx)) = (range_titan.find("["), range_titan.find("]")) {
        let portions = &range_titan[start_idx+1..end_idx];
        for portion in portions.split(",") {
            if portion.contains("-") {
                let dash_idx = portion.find("-").unwrap();
                let a = usize::from_str(&portion[0..dash_idx]).unwrap();
                let b = usize::from_str(&portion[dash_idx+1..]).unwrap();
                let mut total_range = (a..b).collect();
                nodes.append(&mut total_range);
            } else {
                nodes.push(usize::from_str(portion).unwrap());
            }
            
        }

    }
    Ok(nodes)
}


