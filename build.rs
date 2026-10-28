fn main() {
    println!("cargo:rerun-if-env-changed=AIROOT_SETUP_PAYLOAD");
    let out =
        std::path::PathBuf::from(std::env::var_os("OUT_DIR").unwrap()).join("setup-payload.zip");
    if let Some(path) = std::env::var_os("AIROOT_SETUP_PAYLOAD") {
        println!(
            "cargo:rerun-if-changed={}",
            std::path::Path::new(&path).display()
        );
        std::fs::copy(path, out).expect("Copy installer payload");
    } else {
        std::fs::write(out, []).unwrap();
    }
}
