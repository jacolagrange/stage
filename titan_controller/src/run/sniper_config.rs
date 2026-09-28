use std::collections::BTreeMap;
use std::path::PathBuf;
use std::fs::File;
use std::io::Read;

#[derive(Debug, Clone, Hash)]
pub struct SniperConfig{
    map: BTreeMap<String, String>
}

impl SniperConfig {
    pub fn new() -> Self {
        SniperConfig{
            map: BTreeMap::new()
        }
    }

    pub fn parse_file(&mut self, path: &PathBuf) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let mut file = File::open(path)?;
        let mut contents = String::new();
        file.read_to_string(&mut contents)?;
        self.parse_text(&contents)
    }

    pub fn parse_text(&mut self, text: &str) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let mut current_section = String::new();
        
        for (line_num, line) in text.lines().enumerate() {
            let line = line.trim();
            
            // Skip empty lines and comments
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            
            // Handle section headers
            if let (true, Some(end_char)) = (line.starts_with('['), line.find(']')) {
                current_section = line[1..end_char].to_string();
                continue;
            }
            
            // Handle key-value pairs
            if let Some(eq_pos) = line.find('=') {
                let key_part = line[..eq_pos].trim();
                let value_part = line[eq_pos + 1..].trim();
                
                // Remove inline comments
                let value_part = if let Some(comment_pos) = value_part.find('#') {
                    value_part[..comment_pos].trim()
                } else {
                    value_part
                };
                
                // Determine if this is an array key (ends with [])
                let (base_key, _is_array) = if key_part.ends_with("[]") {
                    (&key_part[..key_part.len()-2], true)
                } else {
                    (key_part, false)
                };
                
                // Create full key path
                let full_key = if current_section.is_empty() {
                    base_key.to_string()
                } else {
                    format!("{}/{}", current_section, base_key)
                };
                
                self.map.insert(full_key, value_part.to_owned());
            } else if !line.is_empty() {
                return Err(format!("Invalid syntax on line {}: {}", line_num + 1, line).into());
            }
        }
        Ok(())
    }

    pub fn set_override(&mut self, full_key: &str, value: &str) {
                self.map.insert(full_key.to_string(), value.to_string());
    }
}
