use crate::store::*;
use crate::*;
static SKILL: include_dir::Dir<'_> = include_dir::include_dir!("$CARGO_MANIFEST_DIR/skills/reckon");
pub fn root(h: &Path) -> Result<PathBuf> {
    let root = local_path(h, format!("assets/{}", env!("CARGO_PKG_VERSION")))?;
    materialize(
        &local_path(&root, "WORKSPACE.md")?,
        include_str!("../WORKSPACE.md"),
    )?;
    fn extract(dir: &include_dir::Dir<'_>, target: &Path) -> Result<()> {
        for file in dir.files() {
            materialize(&local_path(target, file.path())?, file.contents())?;
        }
        for child in dir.dirs() {
            extract(child, target)?;
        }
        Ok(())
    }
    extract(&SKILL, &local_path(&root, "skills/reckon")?)?;
    materialize(
        &local_path(&root, "scripts/cli-releases.csv")?,
        include_str!("../scripts/cli-releases.csv"),
    )?;
    Ok(root)
}
fn materialize(path: &Path, bytes: impl AsRef<[u8]>) -> Result<()> {
    if path.is_file() && read_bytes(path, 8 * 1048576)?.as_slice() == bytes.as_ref() {
        return Ok(());
    }
    write(path, bytes)
}
