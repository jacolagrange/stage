use std::fs::File;
use std::io::Write;
use std::path::Path;
use std::collections::HashMap;

pub fn fill_template(mut template: String, dst_path: &Path, replacement_map: &HashMap<String, String>){
    for (old_str, new_str) in replacement_map.into_iter(){
        template = template.replace(&*old_str, &new_str);
    }

    let mut dst_file = File::create(dst_path).unwrap();
    let _ = dst_file.write(template.as_bytes());
}
