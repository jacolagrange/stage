use std::collections::HashMap;

use crate::run::sniper_config::SniperConfig;
use crate::constants::LOCAL_SNIPER_DIR;

#[derive(Debug)]
pub struct ParsedArgs {
    configs: Vec<String>,
    cmd_settings: HashMap<String, String>,
    #[allow(dead_code)]
    others: Vec<String>,
}

impl ParsedArgs{
    pub fn new(input: &str) -> Self {
        let mut configs = vec![String::from("base")];
        let mut cmd_settings = HashMap::new();
        let mut others = Vec::new();
    
        let mut iter = input.split_whitespace().peekable();
    
        while let Some(arg) = iter.next() {
            match arg {
                "-c" => {
                    if let Some(value) = iter.next() {
                        configs.push(value.to_string());
                    }
                }
                "-g" => {
                    if let Some(setting) = iter.next() {
                        // Allow --key=value or key=value
                        let setting = setting.strip_prefix("--").unwrap_or(setting);
                        if let Some((key, value)) = setting.split_once('=') {
                            cmd_settings.insert(key.to_string(), value.to_string());
                        }
                    }
                }
                "-s" => {
                    if let Some(setting) = iter.next() {
                        // Allow --key=value or key=value
                        let setting = setting.strip_prefix("--").unwrap_or(setting);
                        let (key, value) = setting.split_once(":").unwrap_or((setting, ""));
                        cmd_settings.insert(key.to_string(), value.to_string());
                    }
                }
                other => {
                    others.push(other.to_string());
                }
            }
        }
    
        ParsedArgs {
            configs,
            cmd_settings,
            others,
        }
    }

    pub fn get_final_config(&self) -> SniperConfig {
        let mut settings = SniperConfig::new();

        let config_dir = LOCAL_SNIPER_DIR.clone().join("config");
        for config in &self.configs {
            let _ = settings.parse_file(&config_dir.join(format!("{config}.cfg")));
        }

        for (key, val) in &self.cmd_settings {
            settings.set_override(key, val.as_str());
        }
        
        settings
    }
}
