#include <slow5/slow5.h>
#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: bms-slow5-index <blow5>\n");
        return 64;
    }
    slow5_file_t *file = slow5_open(argv[1], "r");
    if (file == NULL) {
        fprintf(stderr, "cannot open BLOW5 input\n");
        return 65;
    }
    int status = slow5_idx_create(file);
    if (slow5_close(file) < 0 && status == 0) {
        status = -1;
    }
    if (status < 0) {
        fprintf(stderr, "cannot create BLOW5 index\n");
        return 1;
    }
    return 0;
}
