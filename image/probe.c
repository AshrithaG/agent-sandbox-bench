/*
 * probe: attempt the operations an agent's generated code should not be able
 * to perform, and report what happened to each.
 *
 * Every probe is a direct system call, not a shell command, so the result says
 * what the kernel (or gVisor's user-space kernel) decided rather than what a
 * missing binary happened to do. Output is one JSON object per line.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/ptrace.h>
#include <sys/syscall.h>
#include <sys/utsname.h>
#include <unistd.h>

/* musl has no strerrorname_np, and the symbolic name is what a reader needs:
 * EPERM and ENOSYS mean the call was stopped, EINVAL and EFAULT mean it got
 * far enough to reject its arguments. */
static const char *errname(int e) {
    switch (e) {
    case EPERM: return "EPERM";
    case EACCES: return "EACCES";
    case ENOSYS: return "ENOSYS";
    case EINVAL: return "EINVAL";
    case EFAULT: return "EFAULT";
    case EROFS: return "EROFS";
    case EBADF: return "EBADF";
    case ENOENT: return "ENOENT";
    case EOPNOTSUPP: return "EOPNOTSUPP";
    case EBUSY: return "EBUSY";
    default: return "OTHER";
    }
}

static void report(const char *probe, long rc, int err, const char *note) {
    printf("{\"probe\":\"%s\",\"allowed\":%s,\"errno\":\"%s\",\"note\":\"%s\"}\n",
           probe, rc >= 0 ? "true" : "false", rc >= 0 ? "" : errname(err), note);
    fflush(stdout);
}

int main(void) {
    struct utsname u;
    uname(&u);
    printf("{\"probe\":\"kernel\",\"allowed\":true,\"errno\":\"\",\"note\":\"%s\"}\n", u.release);

    /* A new user namespace is the first step of most container escapes. */
    long rc = unshare(CLONE_NEWUSER);
    report("unshare_user_ns", rc, errno, "user namespace creation");

    rc = mount("none", "/tmp", "tmpfs", 0, NULL);
    report("mount", rc, errno, "mount a filesystem");

    rc = ptrace(PTRACE_TRACEME, 0, NULL, NULL);
    report("ptrace", rc, errno, "trace a process");

    /* bpf, perf and io_uring are large kernel attack surfaces with a long CVE
     * history; the arguments are deliberately invalid, so EINVAL means the
     * call reached the kernel and EPERM/ENOSYS means it was stopped first. */
    rc = syscall(SYS_bpf, 0, NULL, 0);
    report("bpf", rc, errno, "EINVAL means it reached a kernel");
    rc = syscall(SYS_perf_event_open, NULL, 0, -1, -1, 0);
    report("perf_event_open", rc, errno, "EINVAL/EFAULT means it reached a kernel");
#ifdef SYS_io_uring_setup
    rc = syscall(SYS_io_uring_setup, 0, NULL);
    report("io_uring_setup", rc, errno, "EINVAL/EFAULT means it reached a kernel");
#endif
    rc = syscall(SYS_userfaultfd, 0);
    report("userfaultfd", rc, errno, "used in several kernel exploits");
    rc = syscall(SYS_keyctl, 0, 0, 0, 0, 0);
    report("keyctl", rc, errno, "kernel keyring");

    int fd = open("/proc/kallsyms", O_RDONLY);
    char line[128] = {0};
    int leaked = 0;
    if (fd >= 0) {
        ssize_t n = read(fd, line, sizeof(line) - 1);
        close(fd);
        /* Non-zero addresses in kallsyms defeat kernel ASLR. */
        leaked = n > 16 && strncmp(line, "0000000000000000", 16) != 0;
    }
    report("kallsyms_addresses", leaked ? 0 : -1, leaked ? 0 : EACCES, "non-zero kernel addresses readable");

    fd = open("/etc/probe-write-test", O_CREAT | O_WRONLY, 0644);
    report("write_rootfs", fd, errno, "write outside the scratch dir");
    if (fd >= 0) close(fd);
    return 0;
}
