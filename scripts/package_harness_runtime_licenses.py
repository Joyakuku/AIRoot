"""Collect notices for the Rust executable shipped in the Harness plugin."""
import argparse
import json
import pathlib
import re
import shutil
import subprocess


parser = argparse.ArgumentParser()
parser.add_argument("--cargo", default="cargo")
parser.add_argument(
    "--destination",
    default="integrations/deepseek-harness/runtime/licenses",
)
args = parser.parse_args()

repo = pathlib.Path(__file__).resolve().parent.parent
destination = (repo / args.destination).resolve()
if destination.exists():
    raise FileExistsError(f"Refusing to replace existing notice directory: {destination}")


def command(*argv):
    return subprocess.check_output(argv, cwd=repo, text=True, encoding="utf-8")


cargo = pathlib.Path(args.cargo)
metadata = json.loads(
    command(str(cargo), "metadata", "--locked", "--format-version", "1")
)
tree = command(
    str(cargo),
    "tree",
    "--locked",
    "--target",
    "x86_64-pc-windows-msvc",
    "--edges",
    "normal,build",
    "--prefix",
    "none",
    "--format",
    "{p}",
)
required = {
    (name, version)
    for name, version in re.findall(r"^([\w-]+) v([^\s]+)", tree, re.MULTILINE)
}
packages = {
    (package["name"], package["version"]): package
    for package in metadata["packages"]
}

destination.mkdir(parents=True)
inventory = []
for identity in sorted(required):
    if identity[0] == "airoot":
        continue
    package = packages[identity]
    root = pathlib.Path(package["manifest_path"]).parent
    files = [
        path
        for path in root.iterdir()
        if path.is_file()
        and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
    ]
    if package.get("license_file"):
        license_file = root / package["license_file"]
        if license_file.is_file() and license_file not in files:
            files.append(license_file)
    if not files:
        raise RuntimeError(f"No license notice found for {identity[0]} {identity[1]}")

    package_dir = destination / f"{identity[0]}-{identity[1]}"
    package_dir.mkdir()
    for license_path in files:
        shutil.copy2(license_path, package_dir / license_path.name)
    inventory.append(
        {
            key: package.get(key)
            for key in ("name", "version", "license", "repository", "source")
        }
    )
    if identity[0] == "libsqlite3-sys":
        sources = list(root.rglob("sqlite3.c"))
        if sources:
            text = sources[0].read_text(encoding="utf-8")
            (package_dir / "SQLite-public-domain-notice.txt").write_text(
                "\n".join(text.splitlines()[:40]), encoding="utf-8"
            )

rustc = cargo.with_name("rustc.exe") if cargo.suffix else pathlib.Path("rustc")
sysroot = pathlib.Path(command(str(rustc), "--print", "sysroot").strip())
rust_docs = sysroot / "share" / "doc" / "rust"
shutil.copytree(rust_docs / "licenses", destination / "rust-standard-library")
shutil.copy2(rust_docs / "COPYRIGHT-library.html", destination / "Rust-COPYRIGHT-library.html")
(destination / "dependency-inventory.json").write_text(
    json.dumps(inventory, indent=2), encoding="utf-8"
)
(destination / "README.txt").write_text(
    "AIRoot-owned code is licensed under MIT; see the repository LICENSE. "
    "This does not relicense bundled third-party components or dependencies. "
    "This directory contains notices for the Windows x64 AIRoot executable "
    "and Rust standard library shipped by the DeepSeek Harness plugin. "
    "The dependency inventory records package versions, declared license "
    "expressions, and upstream source locations. Component notices are in "
    "../components/licenses.\n",
    encoding="utf-8",
)
print(destination)
