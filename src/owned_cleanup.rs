//! Failed-install cleanup must delete the object that was verified, not a later
//! occupant of its pathname. No quarantine moves or path-based deletion fallback.
use anyhow::Result;
use std::path::Path;

pub(crate) fn remove(path: &Path, expected_identity: &str) -> Result<()> {
    #[cfg(windows)]
    {
        windows::remove_with(path, expected_identity, || Ok(()))
    }
    #[cfg(not(windows))]
    {
        let _ = (path, expected_identity);
        anyhow::bail!("Automatic owned cleanup requires Windows object-bound deletion")
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use anyhow::Context;
    use std::{
        ffi::OsString,
        fs::{File, OpenOptions},
        os::windows::{ffi::OsStringExt, fs::OpenOptionsExt, io::AsRawHandle},
        path::{Component, Prefix},
    };
    use windows_sys::Win32::Storage::FileSystem::*;

    struct Pinned {
        file: File,
        info: BY_HANDLE_FILE_INFORMATION,
    }
    impl Pinned {
        fn open(path: &Path, deletable: bool) -> Result<Self> {
            // Deny rename, deletion and write handles until this object is closed.
            // OPEN_REPARSE_POINT inspects the entry itself, never its destination.
            let file = OpenOptions::new()
                .access_mode(
                    FILE_READ_ATTRIBUTES
                        | if deletable {
                            DELETE | FILE_LIST_DIRECTORY
                        } else {
                            0
                        },
                )
                .share_mode(FILE_SHARE_READ)
                .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                .open(path)
                .with_context(|| {
                    format!("Cannot pin cleanup object {}; retained", path.display())
                })?;
            let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
            if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
                return Err(std::io::Error::last_os_error())
                    .context("Cannot identify pinned object");
            }
            anyhow::ensure!(
                info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT == 0,
                "Linked cleanup object retained: {}",
                path.display()
            );
            Ok(Self { file, info })
        }
        fn is_dir(&self) -> bool {
            self.info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0
        }
        fn identity(&self) -> String {
            let info = &self.info;
            format!(
                "{}:{}:{}:{}:{}",
                info.dwVolumeSerialNumber,
                info.nFileIndexHigh,
                info.nFileIndexLow,
                info.ftCreationTime.dwHighDateTime,
                info.ftCreationTime.dwLowDateTime
            )
        }
        fn children(&self) -> Result<Vec<(OsString, String)>> {
            // Enumerate file IDs from the pinned directory, not just names. A
            // replacement between enumeration and open must fail identity check.
            let mut buffer = [0u64; 8192];
            let mut children = Vec::new();
            let mut class = FileIdBothDirectoryRestartInfo;
            loop {
                let ok = unsafe {
                    GetFileInformationByHandleEx(
                        self.file.as_raw_handle(),
                        class,
                        buffer.as_mut_ptr().cast(),
                        std::mem::size_of_val(&buffer) as u32,
                    )
                };
                if ok == 0 {
                    let error = std::io::Error::last_os_error();
                    if error.raw_os_error() == Some(18) {
                        // ERROR_NO_MORE_FILES
                        break;
                    }
                    return Err(error)
                        .context("Cannot enumerate pinned directory identities; retained");
                }
                class = FileIdBothDirectoryInfo;
                let mut offset = 0usize;
                loop {
                    let capacity = std::mem::size_of_val(&buffer);
                    anyhow::ensure!(
                        offset + std::mem::size_of::<FILE_ID_BOTH_DIR_INFO>() <= capacity,
                        "Invalid directory identity buffer"
                    );
                    let pointer = unsafe { buffer.as_ptr().cast::<u8>().add(offset) };
                    let entry = unsafe {
                        std::ptr::read_unaligned(pointer.cast::<FILE_ID_BOTH_DIR_INFO>())
                    };
                    let header = std::mem::offset_of!(FILE_ID_BOTH_DIR_INFO, FileName);
                    let length = entry.FileNameLength as usize;
                    anyhow::ensure!(
                        length > 0 && length % 2 == 0 && offset + header + length <= capacity,
                        "Invalid directory entry name"
                    );
                    let name = unsafe {
                        std::slice::from_raw_parts(pointer.add(header).cast::<u16>(), length / 2)
                    };
                    let name = OsString::from_wide(name);
                    if name != "." && name != ".." {
                        anyhow::ensure!(
                            entry.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT == 0,
                            "Linked cleanup child retained"
                        );
                        anyhow::ensure!(
                            matches!(
                                Path::new(&name).components().collect::<Vec<_>>().as_slice(),
                                [Component::Normal(_)]
                            ),
                            "Invalid cleanup child name"
                        );
                        let file_id = entry.FileId as u64;
                        let created = entry.CreationTime as u64;
                        anyhow::ensure!(file_id != 0, "Child file identity unavailable; retained");
                        children.push((
                            name,
                            format!(
                                "{}:{}:{}:{}:{}",
                                self.info.dwVolumeSerialNumber,
                                file_id >> 32,
                                file_id as u32,
                                created >> 32,
                                created as u32
                            ),
                        ));
                        anyhow::ensure!(
                            children.len() <= 100_000,
                            "Cleanup directory too large; retained"
                        );
                    }
                    if entry.NextEntryOffset == 0 {
                        break;
                    }
                    let next = entry.NextEntryOffset as usize;
                    anyhow::ensure!(
                        next >= header + length && offset + next < capacity,
                        "Invalid directory identity offset"
                    );
                    offset += next;
                }
            }
            Ok(children)
        }
        fn delete(self) -> Result<()> {
            let disposition = FILE_DISPOSITION_INFO { DeleteFile: true };
            let mut retries = 0;
            loop {
                // The disposition belongs to this pinned handle, never a pathname.
                let ok = unsafe {
                    SetFileInformationByHandle(
                        self.file.as_raw_handle(),
                        FileDispositionInfo,
                        (&disposition as *const FILE_DISPOSITION_INFO).cast(),
                        std::mem::size_of::<FILE_DISPOSITION_INFO>() as u32,
                    )
                };
                if ok != 0 {
                    return Ok(());
                }
                let error = std::io::Error::last_os_error();
                if error.kind() == std::io::ErrorKind::Interrupted && retries < 2 {
                    retries += 1;
                } else {
                    return Err(error).context("Pinned cleanup deletion blocked");
                }
            }
        }
    }

    fn snapshot(
        path: &Path,
        node: Pinned,
        entries: &mut Vec<Pinned>,
        depth: usize,
        before_child_open: &mut impl FnMut(&Path) -> Result<()>,
    ) -> Result<()> {
        anyhow::ensure!(depth <= 256, "Cleanup tree too deep; retained");
        if node.is_dir() {
            for (name, expected_identity) in node.children()? {
                let child = path.join(name);
                before_child_open(&child)?;
                let pinned = Pinned::open(&child, true)?;
                anyhow::ensure!(
                    pinned.identity() == expected_identity,
                    "Cleanup child replaced after enumeration; retained: {}",
                    child.display()
                );
                snapshot(&child, pinned, entries, depth + 1, before_child_open)?;
            }
        }
        entries.push(node);
        anyhow::ensure!(entries.len() <= 100_000, "Cleanup tree too large; retained");
        Ok(())
    }

    pub(super) fn remove_with(
        path: &Path,
        expected_identity: &str,
        before_delete: impl FnOnce() -> Result<()>,
    ) -> Result<()> {
        remove_with_hooks(path, expected_identity, before_delete, |_| Ok(()))
    }

    fn remove_with_hooks(
        path: &Path,
        expected_identity: &str,
        before_delete: impl FnOnce() -> Result<()>,
        mut before_child_open: impl FnMut(&Path) -> Result<()>,
    ) -> Result<()> {
        anyhow::ensure!(
            path.is_absolute(),
            "Owned cleanup requires an absolute path"
        );
        anyhow::ensure!(
            matches!(path.components().next(), Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)))
                && !path
                    .components()
                    .any(|c| matches!(c, Component::ParentDir | Component::CurDir)),
            "Owned cleanup requires a local disk path without traversal"
        );
        // Pin from the disk root downward. Otherwise an unpinned ancestor could
        // be renamed or changed into a junction while we enumerate descendants.
        let mut ancestors = Vec::new();
        for ancestor in path
            .parent()
            .context("Owned object needs a parent")?
            .ancestors()
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            let pinned = Pinned::open(ancestor, false)?;
            anyhow::ensure!(pinned.is_dir(), "Cleanup ancestor is not a directory");
            ancestors.push(pinned);
        }
        let root = Pinned::open(path, true)?;
        anyhow::ensure!(
            root.identity() == expected_identity,
            "Owned object replaced; retained"
        );
        let mut entries = Vec::new();
        // Finish inspection and acquire every handle before the first deletion.
        snapshot(path, root, &mut entries, 0, &mut before_child_open)?;
        before_delete()?;
        for entry in entries {
            entry.delete()?;
        }
        drop(ancestors);
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use crate::recovery;
        use std::fs;

        #[test]
        fn pinned_cleanup_blocks_root_child_and_ancestor_replacement() {
            let tmp = tempfile::tempdir().unwrap();
            let parent = tmp.path().join("parent");
            let root = parent.join("任务 sample");
            fs::create_dir_all(root.join("nested")).unwrap();
            let child = root.join("nested/keep.txt");
            fs::write(&child, b"owned").unwrap();
            let identity = recovery::identity(&root).unwrap();
            remove_with(&root, &identity, || {
                let root = root.clone();
                let parent = parent.clone();
                let child = child.clone();
                std::thread::spawn(move || {
                    assert!(fs::rename(&root, root.with_extension("moved")).is_err());
                    assert!(fs::rename(&parent, parent.with_extension("moved")).is_err());
                    assert!(fs::rename(&child, child.with_extension("moved")).is_err());
                    assert!(fs::remove_file(&child).is_err());
                    assert!(OpenOptions::new().write(true).open(&child).is_err());
                })
                .join()
                .unwrap();
                Ok(())
            })
            .unwrap();
            assert!(!root.exists());
            assert!(parent.exists());
        }

        #[test]
        fn owned_file_is_deleted_by_its_handle() {
            let tmp = tempfile::tempdir().unwrap();
            let path = tmp.path().join("partial.zip");
            fs::write(&path, b"partial").unwrap();
            let identity = recovery::identity(&path).unwrap();
            super::super::remove(&path, &identity).unwrap();
            assert!(!path.exists());
        }

        #[test]
        fn replaced_file_is_retained_without_creating_quarantine() {
            let tmp = tempfile::tempdir().unwrap();
            let path = tmp.path().join("partial.zip");
            fs::write(&path, b"old").unwrap();
            let identity = recovery::identity(&path).unwrap();
            let original = path.with_extension("old");
            fs::rename(&path, &original).unwrap();
            fs::write(&path, b"replacement").unwrap();
            assert!(super::super::remove(&path, &identity).is_err());
            assert_eq!(fs::read(&path).unwrap(), b"replacement");
            assert_eq!(fs::read(&original).unwrap(), b"old");
            assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 2);
        }

        #[test]
        fn aborted_snapshot_does_not_delete_any_content() {
            let tmp = tempfile::tempdir().unwrap();
            let root = tmp.path().join("owned");
            fs::create_dir(&root).unwrap();
            let child = root.join("first");
            fs::write(&child, b"preserve").unwrap();
            let identity = recovery::identity(&root).unwrap();
            assert!(
                remove_with(&root, &identity, || anyhow::bail!(
                    "simulated exit before mutation"
                ))
                .is_err()
            );
            assert_eq!(fs::read(&child).unwrap(), b"preserve");
            super::super::remove(&root, &identity).unwrap();
            assert!(!root.exists());
        }

        #[test]
        fn replacement_between_enumeration_and_open_is_retained() {
            let tmp = tempfile::tempdir().unwrap();
            let root = tmp.path().join("owned");
            fs::create_dir(&root).unwrap();
            let child = root.join("child");
            fs::write(&child, b"owned").unwrap();
            let identity = recovery::identity(&root).unwrap();
            let original = tmp.path().join("original");
            let result = remove_with_hooks(
                &root,
                &identity,
                || Ok(()),
                |path| {
                    if path == child {
                        fs::rename(path, &original)?;
                        fs::write(path, b"replacement")?;
                    }
                    Ok(())
                },
            );
            assert!(
                result
                    .unwrap_err()
                    .to_string()
                    .contains("replaced after enumeration")
            );
            assert_eq!(fs::read(&child).unwrap(), b"replacement");
            assert_eq!(fs::read(&original).unwrap(), b"owned");
        }

        #[test]
        fn late_added_child_is_not_deleted_and_cleanup_can_retry() {
            let tmp = tempfile::tempdir().unwrap();
            let root = tmp.path().join("owned");
            fs::create_dir(&root).unwrap();
            fs::write(root.join("original"), b"owned").unwrap();
            let late = root.join("late");
            let identity = recovery::identity(&root).unwrap();
            let result = remove_with(&root, &identity, || {
                fs::write(&late, b"late")?;
                Ok(())
            });
            assert!(result.is_err());
            assert!(root.exists());
            assert_eq!(fs::read(&late).unwrap(), b"late");
            // Move the external arrival out, then explicitly retry the same tree.
            fs::rename(&late, tmp.path().join("preserved-late")).unwrap();
            super::super::remove(&root, &identity).unwrap();
            assert!(!root.exists());
        }
    }
}
