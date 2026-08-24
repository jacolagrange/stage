use strum_macros::{AsRefStr, EnumString};

#[derive(Debug, Clone, PartialEq, AsRefStr, EnumString)]
pub enum JobStatus {
    TOSUBMIT,
    SUBMITTED,
    DONE,
    FAILED,
    RETRIED
}
