# agent-sandbox-bench

What does it cost to run an agent's generated code under gVisor instead of a
plain container, and what does it actually stop?

Same image, same code, seven isolation configurations: Docker's default `runc`,
`runc` with every cheap hardening flag, gVisor's `runsc`, `runsc` hardened, and
three ablations that separate the hardening flags from the runtime. Each run
records what the code could do (direct system calls, not shell commands) and how
long agent-shaped work took.

**Setup, stated up front.** An Ubuntu 24.04 arm64 VM under Multipass on an
Apple silicon Mac, Docker 27, gVisor from its signed apt repository, using the
`systrap` platform because there is no `/dev/kvm` in the VM. Seven repetitions
per cell. On an x86 host with the KVM platform the costs will differ, and this
does not measure that.

## What each configuration let the code do

Probes are direct system calls with deliberately invalid arguments where that
matters: `EINVAL`/`EFAULT` means the call reached a kernel, `EPERM` means a
seccomp filter stopped it first, `ENOSYS` means the kernel it reached does not
implement it at all.

| probe | runc default | runc hardened | runsc default | runsc hardened |
|---|---|---|---|---|
| kernel the code sees | host `6.8.0` | host `6.8.0` | `4.19.0-gvisor` | `4.19.0-gvisor` |
| create a user namespace | EPERM | EPERM | allowed, inside gVisor | allowed, inside gVisor |
| `mount` | EPERM | EPERM | EPERM | EPERM |
| `bpf` | EPERM | EPERM | EPERM | EPERM |
| `io_uring_setup` | EPERM | EPERM | ENOSYS | ENOSYS |
| `userfaultfd` | EPERM | EPERM | ENOSYS | ENOSYS |
| `keyctl` | EPERM | EPERM | ENOSYS | ENOSYS |
| write to the root filesystem | EACCES | EROFS | EACCES | EACCES |
| network egress | allowed | blocked | allowed | blocked |
| 600 forks | allowed | stopped cleanly, `EAGAIN` | allowed | **whole sandbox killed** |
| allocate 1.5 GB | allowed | OOM-killed | allowed | OOM-killed |

Reading it:

- The `EPERM` versus `ENOSYS` difference is the argument for gVisor. Under
  `runc`, `io_uring`, `userfaultfd` and `keyctl` are filtered but still exist in
  the host kernel the code is talking to, one seccomp mistake away. Under gVisor
  they do not exist in the kernel the code can reach.
- "Allowed" under gVisor is not the same claim as under `runc`. Creating a user
  namespace succeeds, but gVisor's user-space kernel handles it; it never reaches
  the host.
- Neither runtime stops network egress, fork storms or memory exhaustion by
  default. Those need flags whatever the runtime.

## A limit that behaves differently under gVisor

With `--pids-limit=50`, measured directly:

| runtime | what happened |
|---|---|
| `runc` | the 50th fork returned `EAGAIN`; the program handled it and exited 0 |
| `runsc` | after **10** forks the entire sandbox exited with code 2, no error printed, not an OOM kill |

The pids cgroup counts host processes, and gVisor's own machinery lives in that
cgroup too, so the guest gets a fraction of the budget. Crossing it does not
fail the one `fork` that crossed it; it takes down everything in the sandbox,
including whatever the agent was in the middle of. A limit sized for `runc` is
the wrong size for gVisor, and a runaway `make -j` inside gVisor looks like a
crash with no diagnostic.

The first version of the harness scored this as "allowed", because it treated
empty output as success. That would have been a false and rather striking
claim that gVisor ignores the pids limit. It now distinguishes a clean refusal,
no limit, and a killed sandbox.

## What it cost

Median of seven runs, ratio to `runc` default, range in brackets.

| workload | runc default | runsc default | runsc hardened | runsc, one CPU only |
|---|---|---|---|---|
| cold start (`docker run ... true`) | 158 ms | 175 ms (1.11x) | 129 ms (0.82x) | 215 ms (1.36x) |
| pure Python compute | 121 ms | 120 ms (1.00x) | 151 ms (1.25x) | 148 ms (1.22x) |
| 2,000 small files: create, stat, delete | 61 ms | 66 ms (1.08x) | 124 ms (2.04x) | 102 ms (1.67x) |
| 50 process spawns | 9 ms | 61 ms (7.14x) | 108 ms (12.57x) | 113 ms (13.22x) |
| git init, 200 files, commit | 19 ms | 68 ms (3.48x) | 111 ms (5.73x) | 129 ms (6.64x) |

What the ablations settled:

- **gVisor's own cost is process creation, not compute or file I/O.** Compute is
  free, small-file work is 1.08x, spawning processes is about 7x, and a git
  commit, which is mostly process and syscall churn, is 3.5x.
- **The one-CPU quota, not gVisor, doubles the hardened overhead.** `runsc` with
  only `--cpus=1` lands where `runsc` hardened does. gVisor's user-space kernel
  needs CPU of its own to handle system calls, so a tight CPU quota starves it
  twice.
- **"Hardened `runc` is faster" is a tmpfs artifact.** The hardened bundle mounts
  `/tmp` as tmpfs; `runc` with only that flag shows the same speed-up (0.42x on
  small files). Under gVisor the same flag changes nothing (0.99x), because
  gVisor already keeps the container's writes in its own memory.

Full table, all seven configurations: `python3 bench/analyze.py results`.

## Limits

- One VM, one architecture, the `systrap` platform. No KVM platform, no x86.
- Seven repetitions. Some cells have long tails, shown in the ranges; the
  medians are what the conclusions rest on.
- The probes test whether a call is reachable, not whether an exploit works.
  Nothing here attempts an escape.
- Firecracker and Kata need `/dev/kvm`, which this VM does not have, so the
  comparison stops at gVisor.

## Running it

On a Linux host with Docker and gVisor installed:

```bash
docker build -t agent-sandbox:bench image
sudo python3 bench/run.py 7
python3 bench/analyze.py results
```
