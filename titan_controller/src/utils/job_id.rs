use std::str::FromStr;

#[derive(Debug, Clone)]
pub struct JobIds{
    pub ids: Vec<usize>
}

impl JobIds{
    pub fn parse_jobids(arg: &str) -> Result<Self, std::num::ParseIntError> {
        let mut ids = Vec::<usize>::new();
        for part in arg.split(",") {
            if part.contains("-") {
                let extremes: Vec<&str> = part.split("-").collect();
                let first = usize::from_str(extremes[0])?;
                let last = usize::from_str(extremes[1])? + 1;
                let mut vals = (first..last).collect();
                ids.append(&mut vals);
            } else {
                ids.push(usize::from_str(part)?);
            }
        }
        Ok(JobIds{ids})
    }
}


