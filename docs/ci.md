# CI architecture

GitHub-hosted Ubuntu runners normally use glibc, and an AArch64 runner alone is
therefore not an Android runtime. The acceptance job runs inside the official
[`termux/termux-docker`](https://github.com/termux/termux-docker) AArch64 image,
which supplies Bionic libc, Android's linker, AOSP libraries and a Termux
filesystem layout.

```text
ubuntu-24.04-arm GitHub runner
└── pinned termux/termux-docker image
    ├── install the small Termux build toolset
    ├── download and verify official Claude + pinned Bionic Bun
    ├── extract, adapt and graft the standalone module graph
    ├── execute the resulting Bionic AArch64 ELF
    └── render and recognize a real Claude Code TUI frame through a PTY
```

The image is pinned by OCI digest rather than a mutable tag. The job checks the
candidate's version, graft closure and TUI result before its reports can reach
the release job. `SKIP_RUN=1` is not used in this path.

This is a real Bionic userspace, but not a complete physical Android device: it
does not provide Dalvik/ART, application lifecycle management, Doze or vendor
ROM behavior. A physical device remains the reference when changing the Bun
base, minimum Android API or functionality that depends on Android framework
services. Routine Claude graph updates no longer require a maintainer to gate
the release manually.

## Triggers

- Pull requests run the regression suite without handling the proprietary
  Claude binary.
- Pushes to `main` run regression tests plus the full Bionic acceptance job.
- The daily schedule resolves the official latest version and starts the
  Bionic job only when that version has not been released by this repository.
- `workflow_dispatch` can test `latest` or an explicit semantic version.

## Publication boundary

The build job has read-only repository permission. `dist/claude` exists only
inside the ephemeral CI workspace and is never selected by the artifact upload.
Only JSON/text reports smaller than 1 MiB are passed to the release job. The
release job is the sole job with `contents: write`, and its own allowlist and
size guard prevent the modified proprietary binary from being published.
