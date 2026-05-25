# Overrides

Overrides let you replace files from the official Coder repository before the
build starts.

The `overrides/` directory mirrors paths from `coder/coder`.

Example:

```text
overrides/
`-- site/
    `-- src/
        `-- modules/
            `-- resources/
                `-- VSCodeDesktopButton/
                    `-- VSCodeDesktopButton.tsx
```

That file replaces:

```text
site/src/modules/resources/VSCodeDesktopButton/VSCodeDesktopButton.tsx
```

inside the temporary Coder worktree.

## Rules

- `.gitkeep` and `.DS_Store` are ignored.
- Paths are interpreted relative to `overrides/`.
- Paths containing `..` are rejected.
- Override targets must remain inside the Coder worktree.
- Symlinks that point outside `overrides/` are rejected.

## Usage

```bash
python3 build-coder-in-docker.py --overrides-dir overrides --tag dev
```

Use a different override directory for experiments:

```bash
python3 build-coder-in-docker.py --overrides-dir /tmp/coder-overrides --tag test
```
