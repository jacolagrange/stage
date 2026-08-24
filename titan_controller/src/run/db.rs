use std::path::{Path, PathBuf};
use std::str::FromStr;
use rusqlite::{params, Connection, OptionalExtension, Result as SqliteResult};

use super::experiment::Experiment;
use super::benchmark_suite::BenchmarkSuite;
use super::status::JobStatus;
use crate::constants::{CACHE_DB_NAME, CACHE_FOLDER_NAME};

pub struct ExperimentsDataBase {
    conn: Connection,
}

impl ExperimentsDataBase {
    pub fn new() -> SqliteResult<Self> {
        let host_dst_path = CACHE_FOLDER_NAME.clone();
        let db_path = host_dst_path.join(CACHE_DB_NAME);

        if !(host_dst_path.exists() && host_dst_path.is_dir()) {
            std::fs::create_dir_all(&host_dst_path)
                .map_err(|e| rusqlite::Error::SqliteFailure(
                    rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_IOERR),
                    Some(format!("Failed to create directory: {}", e))
                ))?;
        }
        
        let conn = Connection::open(&db_path)?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS experiments (
                path TEXT PRIMARY KEY,
                job_id TEXT,
                task_idx INTEGER,
                status TEXT NOT NULL
            );",
        )?;
        Ok(ExperimentsDataBase { conn })
    }

    pub fn insert(&self, loc: &Path) -> SqliteResult<()> {
        self.conn.execute(
            "INSERT OR IGNORE INTO experiments (path, job_id, task_idx, status)
             VALUES (?1, NULL, NULL, ?2)",
            params![loc.to_string_lossy().as_ref(), JobStatus::TOSUBMIT.as_ref()],
        )?;
        Ok(())
    }

    pub fn add_new_experiment(&self, exp: &Experiment) -> SqliteResult<()> {
        exp.for_each_run_path(|path| {
            // INSERT OR IGNORE will only insert if the path doesn't already exist
            let _ = self.insert(path);
        });
        Ok(())
    }

    pub fn get_status(&self, loc: &Path) -> SqliteResult<Option<JobStatus>> {
        self.conn
            .query_row(
                "SELECT status FROM experiments WHERE path = ?1",
                params![loc.to_string_lossy().as_ref()],
                |row| {
                    let status_str: String = row.get(0)?;
                    JobStatus::from_str(&status_str)
                        .map_err(|_| rusqlite::Error::InvalidQuery)
                },
            )
            .optional()
    }

    pub fn set_status(&self, loc: &Path, new_status: &JobStatus) -> SqliteResult<()> {
        self.conn.execute(
            "UPDATE experiments SET status = ?1 WHERE path = ?2",
            params![new_status.as_ref(), loc.to_string_lossy().as_ref()],
        )?;
        Ok(())
    }

    pub fn set_job_id(&self, loc: &Path, job_id: &str) -> SqliteResult<()> {
        self.conn.execute(
            "UPDATE experiments SET job_id = ?1 WHERE path = ?2",
            params![job_id, loc.to_string_lossy().as_ref()],
        )?;
        Ok(())
    }

    pub fn set_task_id(&self, loc: &Path, task_idx: &usize) -> SqliteResult<()> {
        self.conn.execute(
            "UPDATE experiments SET task_idx = ?1 WHERE path = ?2",
            params![task_idx, loc.to_string_lossy().as_ref()],
        )?;
        Ok(())
    }

    pub fn set_experiment_status(&self, exp: &Experiment, new_status: &JobStatus) -> SqliteResult<()> {
        exp.for_each_run_path(|path| {
            let _ = self.set_status(path, new_status);
        });
        Ok(())
    }

    pub fn set_bench_suite_job_id(&self, bench_suite: &BenchmarkSuite, job_id: &str, new_status: &Option<JobStatus>) -> SqliteResult<()> {
        match new_status {
            Some(status) => {
                // Update both job_id and status in one query
                bench_suite.for_each_run_path(&mut |path: &Path| {
                    let _ = self.conn.execute(
                        "UPDATE experiments SET job_id = ?1, status = ?2 WHERE path = ?3",
                        params![job_id, status.as_ref(), path.to_string_lossy().as_ref()],
                    );
                });
            }
            None => {
                // Only update job_id
                bench_suite.for_each_run_path(&mut |path: &Path| {
                    let _ = self.set_job_id(path, job_id);
                });
            }
        }
        Ok(())
    }

    pub fn get_job_task_format(&self, loc: &Path) -> SqliteResult<Option<String>> {
        self.conn
            .query_row(
                "SELECT job_id, task_idx FROM experiments WHERE path = ?1",
                params![loc.to_string_lossy().as_ref()],
                |row| {
                    let job_id: Option<String> = row.get(0)?;
                    let task_idx: Option<usize> = row.get(1)?;
                    
                    match (job_id, task_idx) {
                        (Some(job_id), Some(task_idx)) => {
                            Ok(Some(format!("{}_{}", job_id, task_idx)))
                        }
                        _ => Ok(None),
                    }
                },
            )
            .optional()
            .map(|opt| opt.flatten()) // Convert Option<Option<String>> to Option<String>
    }

    /// Get all paths with a specific status
    #[allow(dead_code)]
    pub fn get_paths_with_status(&self, status: &JobStatus) -> SqliteResult<Vec<PathBuf>> {
        let mut stmt = self.conn.prepare(
            "SELECT path FROM experiments WHERE status = ?1"
        )?;
        let rows = stmt.query_map(params![status.as_ref()], |row| {
            let path_str: String = row.get(0)?;
            Ok(PathBuf::from(path_str))
        })?;

        let mut results = Vec::new();
        for row in rows {
            results.push(row?);
        }
        Ok(results)
    }

    /// Get all entries (useful for debugging or migration)
    #[allow(dead_code)]
    pub fn get_all_entries(&self) -> SqliteResult<Vec<(PathBuf, Option<String>, Option<usize>, JobStatus)>> {
        let mut stmt = self.conn.prepare(
            "SELECT path, job_id, task_idx, status FROM experiments"
        )?;
        let rows = stmt.query_map([], |row| {
            let path_str: String = row.get(0)?;
            let job_id: Option<String> = row.get(1)?;
            let task_idx: Option<usize> = row.get(2)?;
            let status_str: String = row.get(3)?;
            let status = JobStatus::from_str(&status_str)
                .map_err(|_| rusqlite::Error::InvalidQuery)?;
            
            Ok((PathBuf::from(path_str), job_id, task_idx, status))
        })?;

        let mut results = Vec::new();
        for row in rows {
            results.push(row?);
        }
        Ok(results)
    }
}
