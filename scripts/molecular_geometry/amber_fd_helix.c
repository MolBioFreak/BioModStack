/* SPDX-License-Identifier: GPL-3.0-or-later
 * BMS native fd_helix launcher, Copyright (C) 2026 BMS contributors.
 * No warranty. See COPYING. This launcher does not implement helix geometry.
 * AmberTools24's header incorrectly declares a molecule return value. Use the
 * actual source signature instead: fd_helix writes PDB to stdout, returns void.
 */
#include <stdio.h>
#include <string.h>
extern void fd_helix(char *, char *);
int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: amber-fd-helix FORM SEQUENCE\n"); return 2; }
    const char *alphabet;
    if (!strcmp(argv[1], "abdna") || !strcmp(argv[1], "adna")) alphabet = "ACGT";
    else if (!strcmp(argv[1], "arna")) alphabet = "ACGU";
    else { fprintf(stderr, "unsupported explicit helix form\n"); return 2; }
    if (!*argv[2] || strlen(argv[2]) > 999) return 2; /* PDB residue width, two strands */
    for (const char *p = argv[2]; *p; ++p) if (!strchr(alphabet, *p)) return 2;
    (void)fd_helix(argv[1], argv[2]);
    return ferror(stdout) ? 1 : 0;
}
