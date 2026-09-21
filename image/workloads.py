"""Workloads shaped like what a coding agent actually runs, timed inside the
sandbox. Each prints one JSON line with its wall time.

The mix is deliberate: gVisor's cost is concentrated in system calls, so a
compute-only benchmark would make it look free and a syscall-only one would
make it look ruinous. An agent does both.
"""
import json, os, subprocess, sys, tempfile, time


def timed(name, fn):
    t = time.perf_counter()
    fn()
    print(json.dumps({"workload": name, "seconds": time.perf_counter() - t}), flush=True)


def cpu():
    # Pure interpreter work: what a unit test that does arithmetic costs.
    s = 0
    for i in range(3_000_000):
        s += i * i % 7


def small_files():
    # Create, stat, read and delete many small files: a build, a test run,
    # a package install. This is where a user-space kernel pays.
    d = tempfile.mkdtemp()
    for i in range(2000):
        p = os.path.join(d, f"f{i}.txt")
        with open(p, "w") as f:
            f.write("x" * 64)
        os.stat(p)
    for i in range(2000):
        os.remove(os.path.join(d, f"f{i}.txt"))
    os.rmdir(d)


def spawn():
    # Process creation: agents shell out constantly.
    for _ in range(50):
        subprocess.run(["true"], check=True)


def git_repo():
    # A realistic agent step: make a repo, write files, commit.
    d = tempfile.mkdtemp()
    env = dict(os.environ, GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@x",
               GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@x")
    subprocess.run(["git", "init", "-q", d], check=True, env=env)
    for i in range(200):
        with open(os.path.join(d, f"m{i}.py"), "w") as f:
            f.write(f"def f{i}():\n    return {i}\n")
    subprocess.run(["git", "-C", d, "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", d, "commit", "-qm", "init"], check=True, env=env)


if __name__ == "__main__":
    which = sys.argv[1:] or ["cpu", "small_files", "spawn", "git_repo"]
    for w in which:
        timed(w, globals()[w])
