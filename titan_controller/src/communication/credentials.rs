use std::fs::File;
use std::io::prelude::*;
use crate::constants::ID_FILE;

#[derive(Debug)]
pub struct Credentials{
    pub username: String,
    pub password: String
}

impl Credentials {
    pub fn new() -> std::io::Result<Self> {
        let mut file = File::open(&*ID_FILE)?;
        let mut content = String::new();
        file.read_to_string(&mut content)?;
        let creds: Vec<&str> = content.split(':').collect();
        
        if creds.len() != 2 {
            eprintln!("Problem reading in the credentials from the \".id\" file.");
            eprintln!("Expected format: test_user:password");
            std::process::exit(1);
        }
        
        Ok(Credentials{
            username: String::from(creds[0]), 
            password: String::from(creds[1])
        })
    }
}
