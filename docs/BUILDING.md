# Building the Windows EXE

BAM ships as an **onedir** PyInstaller bundle: `dist/bam/bam.exe` plus an
`_internal/` folder. The EXE runs **without Python installed**.

## Prerequisites

- Windows 10/11
- [uv](https://docs.astral.sh/uv/)

PyInstaller is declared as a **dev dependency** (Windows only) in
`pyproject.toml`, so a normal `uv sync` provides the exact build toolchain.
Do not build with a global `pip install pyinstaller`: it analyzes the project
with the wrong interpreter and can silently omit runtime dependencies
(this actually happened with `httpx`).

## Build

```bash
uv sync
uv run pyinstaller bam.spec --noconfirm
```

Output: `dist/bam/` — entry point `bam.exe`.

The build is driven by the versioned `bam.spec`. Key properties:

- **onedir** (not onefile): fast startup and fewer antivirus false positives.
- **No data files are bundled.** `config/` and `data/` are resolved *next to
  the executable* at runtime (`bam/config.py` is frozen-aware). Secrets, the
  production database and the denylist policy stay external and editable.
- Nothing under `data/`, `backups/` or `tests/` can enter the bundle.

## First run of the bundle

1. Copy `dist/bam/` to the target folder.
2. Copy the repo's `config/` next to `bam.exe`
   (`bam.exe` looks for `./config/*.yaml` relative to itself).
3. Create an empty `data/` folder next to `bam.exe` and put
   `denylist.yaml` in it. The SQLite database is created on first use.
4. Sanity check: `bam.exe doctor` (services may report FAIL if their
   interpreters are not installed on the machine — that is reported, not hidden).

`BAM_ROOT` can relocate the whole tree, exactly as in development.

## Notes

- Some antivirus products flag unsigned PyInstaller bundles. Verify the hash
  of `dist/bam/bam.exe` before trusting a binary you did not build yourself.
- To rebuild reproducibly: `rm -rf build dist` first; the spec pins no
  timestamps but the resulting tree is deterministic for the same lockfile.
- There is no macOS/Linux build; BAM is Windows-first by design.
