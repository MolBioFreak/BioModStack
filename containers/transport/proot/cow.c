/* SPDX-License-Identifier: GPL-2.0-or-later
 * Lazy private-inode materialization. Never hand a donor FD to a tracee.
 * PRoot's single event loop completes materialization before resuming a guest.
 * Inode keys preserve hardlink/rename identity. No per-file descriptor inventory.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <uthash.h>
#include "extension/extension.h"
#include "tracee/reg.h"
#include "syscall/sysnum.h"

#define BMS_FICLONE 0x40049409

typedef struct { uint64_t dev, ino; } Key;
typedef struct CowFile {
    Key key;
    struct stat donor_stat;
    char *lower;
    int copied;
    UT_hash_handle hh;
} CowFile;
static CowFile *files;

static Key key_of(const struct stat *s) {
    Key k = {s->st_dev, s->st_ino};
    return k;
}
static int unchanged(const struct stat *a, const struct stat *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino &&
        a->st_mode == b->st_mode && a->st_nlink == b->st_nlink &&
        a->st_size == b->st_size &&
        a->st_mtim.tv_sec == b->st_mtim.tv_sec && a->st_mtim.tv_nsec == b->st_mtim.tv_nsec &&
        a->st_ctim.tv_sec == b->st_ctim.tv_sec && a->st_ctim.tv_nsec == b->st_ctim.tv_nsec;
}
static int copy_up(CowFile *f, const char *upper_path) {
    int src = -1, dst = -1, result = 0;
    struct stat before, after, upper;
    struct timespec times[2];
    char data[131072];
    off_t offset = 0;
    ssize_t n;
    if (f->copied) return 0;
    src = open(f->lower, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (src < 0) return -errno;
    if (fstat(src, &before) || !unchanged(&before, &f->donor_stat)) {
        result = -ESTALE; goto out;
    }
    if (stat(upper_path, &upper)) { result = -errno; goto out; }
    if (chmod(upper_path, (upper.st_mode & 07777) | 0600)) { result = -errno; goto out; }
    dst = open(upper_path, O_WRONLY | O_CLOEXEC);
    if (dst < 0) { result = -errno; chmod(upper_path, upper.st_mode & 07777); goto out; }
    if (ioctl(dst, BMS_FICLONE, src)) {
        if (errno != EXDEV && errno != EOPNOTSUPP && errno != ENOTTY &&
                errno != EINVAL && errno != ENOSYS) { result = -errno; goto restore; }
        while (offset < before.st_size) {
            n = pread(src, data, sizeof(data), offset);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) { result = -(n < 0 ? errno : EIO); goto restore; }
            ssize_t done = 0;
            while (done < n) {
                ssize_t wrote = pwrite(dst, data + done, n - done, offset + done);
                if (wrote < 0 && errno == EINTR) continue;
                if (wrote <= 0) { result = -(wrote < 0 ? errno : EIO); goto restore; }
                done += wrote;
            }
            offset += n;
        }
        if (ftruncate(dst, before.st_size)) { result = -errno; goto restore; }
    }
    if (fstat(src, &after) || !unchanged(&before, &after)) { result = -ESTALE; goto restore; }
    f->copied = 1;
restore:
    times[0] = upper.st_atim; times[1] = upper.st_mtim;
    if (futimens(dst, times) && !result) result = -errno;
    if (fchmod(dst, upper.st_mode & 07777) && !result) result = -errno;
out:
    if (dst >= 0) close(dst);
    if (src >= 0) close(src);
    return result;
}
static int load_map(const char *path) {
    FILE *stream = fopen(path, "rb");
    char *upper = NULL, *lower = NULL;
    size_t a = 0, b = 0;
    int result = 0;
    if (!stream) return -errno;
    while (getdelim(&upper, &a, 0, stream) >= 0) {
        struct stat s;
        CowFile *f;
        if (getdelim(&lower, &b, 0, stream) < 0 || upper[0] != '/' || lower[0] != '/') {
            result = -EINVAL; break;
        }
        f = calloc(1, sizeof(*f));
        if (!f) { result = -ENOMEM; break; }
        if (lstat(upper, &s) || !S_ISREG(s.st_mode) || lstat(lower, &f->donor_stat) ||
                !S_ISREG(f->donor_stat.st_mode) || s.st_size != f->donor_stat.st_size ||
                (s.st_dev == f->donor_stat.st_dev && s.st_ino == f->donor_stat.st_ino)) {
            free(f); result = -EINVAL; break;
        }
        f->key = key_of(&s);
        f->lower = strdup(lower);
        HASH_ADD(hh, files, key, sizeof(f->key), f);
    }
    if (ferror(stream) && !result) result = -EIO;
    free(upper); free(lower); fclose(stream);
    return result;
}
int cow_callback(Extension *extension, ExtensionEvent event, intptr_t data1, intptr_t data2) {
    (void)extension; (void)data2;
    switch (event) {
    case INITIALIZATION: return load_map((const char *)data1);
    case INHERIT_PARENT: return 1;
    case TRANSLATED_PATH: {
        struct stat s;
        CowFile *f;
        Key key;
        if (stat((char *)data1, &s) || !S_ISREG(s.st_mode)) return 0;
        key = key_of(&s);
        HASH_FIND(hh, files, &key, sizeof(key), f);
        if (!f || f->copied) return 0;
        /* Stat/access alone need no content. All descriptor-producing paths,
         * including O_PATH, and PRoot's ELF/shebang translations come here. */
        switch (get_sysnum(TRACEE(extension), ORIGINAL)) {
        case PR_stat: case PR_lstat: case PR_stat64: case PR_lstat64:
        case PR_newfstatat: case PR_statx: case PR_access: case PR_faccessat:
        case PR_readlink: case PR_readlinkat: return 0;
        default: return copy_up(f, (const char *)data1);
        }
    }
    default: return 0;
    }
}
