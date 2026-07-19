BEGIN {
    line_width = 80
}

function emit_seq(chr, seq,   rem) {
    print ">" chr
    while (length(seq) > line_width) {
        print substr(seq, 1, line_width)
        seq = substr(seq, line_width + 1)
    }
    if (length(seq) > 0) print seq
}

function add_base(base) {
    base = toupper(base)
    if (length(base) == 1 && index("ACGTN", base) > 0) {
        counts[base]++
    }
}

function parse_bases(bases, ref,   i, c, n, len) {
    i = 1
    while (i <= length(bases)) {
        c = substr(bases, i, 1)
        if (c == "^") {
            i += 2
            continue
        }
        if (c == "$") {
            i++
            continue
        }
        if (c == "+" || c == "-") {
            i++
            n = ""
            while (i <= length(bases)) {
                c = substr(bases, i, 1)
                if (c ~ /[0-9]/) {
                    n = n c
                    i++
                } else {
                    break
                }
            }
            len = (n == "") ? 0 : (n + 0)
            i += len
            continue
        }
        if (c == "." || c == ",") {
            add_base(ref)
        } else if (c == "*" || c == "#") {
            # deletion placeholder for padded pileups
        } else {
            add_base(c)
        }
        i++
    }
}

{
    chr = $1
    ref = toupper($3)
    bases = $5

    if (!(chr in seen_chr)) {
        seen_chr[chr] = 1
        chr_count++
        chr_order[chr_count] = chr
    }

    delete counts
    parse_bases(bases, ref)
    best_base = "N"
    best_count = -1
    for (b in counts) {
        if (counts[b] > best_count || (counts[b] == best_count && b < best_base)) {
            best_base = b
            best_count = counts[b]
        }
    }
    if (best_count < 0) best_base = "N"
    consensus_seq[chr] = consensus_seq[chr] best_base
}

END {
    for (i = 1; i <= chr_count; i++) {
        chr = chr_order[i]
        emit_seq(chr, consensus_seq[chr])
    }
}
