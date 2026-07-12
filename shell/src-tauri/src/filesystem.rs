// Native file picker + scoped file handles — engineering-spec §1.3, design-doc §9.
//
// SECURITY PROPERTY: the Agent Core never receives a raw path it can wander with.
// It gets a handle to whatever the OS-native file picker returned, so it
// structurally cannot read/write outside the user's selection. This is the file
// tools' (read_file, save_file) only route to the filesystem.

// TODO(step 7):
//  - pick_file() -> scoped read handle        : OS open dialog
//  - save_new_file(name, bytes) -> path       : OS save dialog; MUST refuse overwrite (§7.4.1)
//  - delete_file(path)                        : undo path for save_file (§7.9)
//  - read_scoped_file(handle) -> bytes        : reads only within a granted handle
