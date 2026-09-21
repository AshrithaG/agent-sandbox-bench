#!/usr/bin/env python3
"""Run the same image under each isolation level and record what it could do
and what it cost. Runs on a Linux host with Docker and gVisor's runsc.

Every result is a JSON line in results/<config>.jsonl. Nothing is summarised
here; analyze.py does that from the records, so the numbers can be re-derived.
"""
import json, statistics, subprocess, sys, time
from pathlib import Path

IMAGE = "agent-sandbox:bench"
REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 5

HARDENED = [
    "--network=none", "--read-only", "--tmpfs", "/tmp:rw,size=256m",
    "--cap-drop=ALL", "--security-opt=no-new-privileges",
    "--pids-limit=128", "--memory=512m", "--memory-swap=512m", "--cpus=1",
]

CONFIGS = {
    # What `docker run` gives an agent if nobody thinks about it.
    "runc-default": [],
    # The same kernel, with every Docker knob that costs nothing turned on.
    "runc-hardened": HARDENED,
    # A user-space kernel between the code and the host, default flags.
    "runsc-default": ["--runtime=runsc"],
    # Both.
    "runsc-hardened": ["--runtime=runsc"] + HARDENED,
    # Ablations. "Hardened" changes several things at once, and two of them
    # move the cost numbers on their own: a tmpfs /tmp takes small-file work off
    # overlayfs, and a one-CPU quota starves gVisor's user-space kernel, which
    # needs CPU of its own to handle system calls. Each is isolated here so the
    # overhead of gVisor itself can be separated from the flags around it.
    "runc-tmpfs": ["--tmpfs", "/tmp:rw,size=256m"],
    "runsc-tmpfs": ["--runtime=runsc", "--tmpfs", "/tmp:rw,size=256m"],
    "runsc-cpus1": ["--runtime=runsc", "--cpus=1"],
}

# Abuse probes that are not single system calls. Each is bounded on its own
# terms so that the unhardened configurations cannot take the host down: the
# point is to learn whether a limit exists, not to exhaust the machine.
FORK = ("import os,sys\n"
        "n=0\n"
        "try:\n"
        "  for _ in range(600):\n"
        "    if os.fork()==0:\n"
        "      import time; time.sleep(3); os._exit(0)\n"
        "    n+=1\n"
        "except OSError as e: print('stopped', n, e.errno); sys.exit(0)\n"
        "print('reached cap', n)")
MEM = ("import sys\n"
       "b=[]\n"
       "for i in range(24):\n"
       "  b.append(bytearray(64*1024*1024)); print((i+1)*64, flush=True)\n"
       "print('reached cap')")


def docker(args, cmd, timeout=180):
    t = time.perf_counter()
    p = subprocess.run(["docker", "run", "--rm", *args, IMAGE, *cmd],
                       capture_output=True, text=True, timeout=timeout)
    return p, time.perf_counter() - t


def main():
    out_dir = Path("results")
    out_dir.mkdir(exist_ok=True)
    for name, args in CONFIGS.items():
        rows = []
        def emit(**kw):
            kw["config"] = name
            rows.append(kw)

        p, _ = docker(args, ["probe"])
        for line in p.stdout.splitlines():
            if line.startswith("{"):
                emit(kind="probe", **json.loads(line))

        p, _ = docker(args, ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", "https://example.com"])
        ok = p.stdout.strip() not in ("", "000")
        emit(kind="abuse", probe="network_egress", allowed=ok, errno="" if ok else "NO_ROUTE",
             note=f"http {p.stdout.strip() or 'none'}")

        # Three outcomes, not two. The first version scored empty output as
        # "allowed", which turned gVisor killing the whole sandbox into a
        # false claim that gVisor ignores the pids limit.
        p, _ = docker(args, ["python3", "-c", FORK])
        out = p.stdout.strip()
        if out.startswith("stopped"):
            emit(kind="abuse", probe="fork_600", allowed=False, errno="EAGAIN",
                 note=f"fork failed cleanly: {out}", exit=p.returncode)
        elif out.startswith("reached cap"):
            emit(kind="abuse", probe="fork_600", allowed=True, note=out, exit=p.returncode)
        else:
            emit(kind="abuse", probe="fork_600", allowed=False, errno="SANDBOX_KILLED",
                 note=f"whole sandbox exited {p.returncode} with no output", exit=p.returncode)

        p, _ = docker(args, ["python3", "-c", MEM])
        lines = [l for l in p.stdout.splitlines() if l.strip()]
        capped = lines[-1] != "reached cap" if lines else True
        emit(kind="abuse", probe="memory_1536mb", allowed=not capped,
             errno=("OOM_KILLED" if p.returncode == 137 else f"exit {p.returncode}") if capped else "",
             note=f"exit {p.returncode}, got to {lines[-1] if lines else 'none'} MB", exit=p.returncode)

        for r in range(REPS):
            _, secs = docker(args, ["true"])
            emit(kind="cost", workload="cold_start", seconds=secs, rep=r)
            p, _ = docker(args, ["python3", "/usr/local/bin/workloads.py"], timeout=600)
            for line in p.stdout.splitlines():
                if line.startswith("{"):
                    d = json.loads(line)
                    emit(kind="cost", workload=d["workload"], seconds=d["seconds"], rep=r)

        path = out_dir / f"{name}.jsonl"
        path.write_text("".join(json.dumps(x) + "\n" for x in rows))
        costs = [x["seconds"] for x in rows if x["kind"] == "cost" and x["workload"] == "small_files"]
        print(f"{name}: {len(rows)} records, small_files median "
              f"{statistics.median(costs):.3f}s" if costs else f"{name}: {len(rows)} records", flush=True)


if __name__ == "__main__":
    main()
