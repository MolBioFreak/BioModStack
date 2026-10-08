BEGIN { OFS = "\t" }

function ref_span(cigar,   i, c, n, span) {
    n = ""; span = 0
    for (i = 1; i <= length(cigar); i++) {
        c = substr(cigar, i, 1)
        if (c ~ /[0-9]/) {
            n = n c
            continue
        }
        if (n == "") continue
        if (c ~ /[MDN=X]/) span += (n + 0)
        n = ""
    }
    return span
}

function query_span(cigar,   i, c, n, span) {
    n = ""; span = 0
    for (i = 1; i <= length(cigar); i++) {
        c = substr(cigar, i, 1)
        if (c ~ /[0-9]/) {
            n = n c
            continue
        }
        if (n == "") continue
        if (c ~ /[MI=X]/) span += (n + 0)
        n = ""
    }
    return span
}

function leading_clip(cigar,   i, c, digits) {
    digits = ""
    for (i = 1; i <= length(cigar); i++) {
        c = substr(cigar, i, 1)
        if (c ~ /[0-9]/) {
            digits = digits c
            continue
        }
        if ((c == "S" || c == "H") && digits != "") return digits + 0
        return 0
    }
    return 0
}

function trailing_clip(cigar,   i, c, digits) {
    if (cigar == "") return 0
    c = substr(cigar, length(cigar), 1)
    if (c != "S" && c != "H") return 0
    digits = ""
    for (i = length(cigar) - 1; i >= 1; i--) {
        c = substr(cigar, i, 1)
        if (c ~ /[0-9]/) {
            digits = c digits
        } else {
            break
        }
    }
    return digits == "" ? 0 : (digits + 0)
}

function abs(v) { return v < 0 ? -v : v }

function clear_segments(   k) {
    for (k in seg_qstart) delete seg_qstart[k]
    for (k in seg_qend) delete seg_qend[k]
    for (k in seg_rstart) delete seg_rstart[k]
    for (k in seg_rend) delete seg_rend[k]
    for (k in seg_span) delete seg_span[k]
    for (k in seg_strand) delete seg_strand[k]
    for (k in seg_idx) delete seg_idx[k]
}

function flush_read(   i, j, tmp, a, b, q_gap, left_ref, right_ref, support_bp, pos_mod, orientation) {
    if (curr_read == "" || seg_count < 2) {
        clear_segments()
        return
    }

    for (i = 1; i <= seg_count; i++) seg_idx[i] = i
    for (i = 2; i <= seg_count; i++) {
        j = i
        while (j > 1 && seg_qstart[seg_idx[j - 1]] > seg_qstart[seg_idx[j]]) {
            tmp = seg_idx[j - 1]
            seg_idx[j - 1] = seg_idx[j]
            seg_idx[j] = tmp
            j--
        }
    }

    best_support = -1
    best_gap = 999999
    best_left = 0
    best_right = 0
    best_a = 0
    best_b = 0

    for (i = 1; i < seg_count; i++) {
        a = seg_idx[i]
        b = seg_idx[i + 1]
        q_gap = seg_qstart[b] - seg_qend[a] - 1
        if (abs(q_gap) > max_gap) continue

        left_ref = seg_rend[a]
        right_ref = seg_rstart[b]
        support_bp = seg_span[a] < seg_span[b] ? seg_span[a] : seg_span[b]
        if (support_bp <= 0) continue

        if (support_bp > best_support \
            || (support_bp == best_support && abs(q_gap) < abs(best_gap)) \
            || (support_bp == best_support && abs(q_gap) == abs(best_gap) && right_ref < best_right)) {
            best_support = support_bp
            best_gap = q_gap
            best_left = left_ref
            best_right = right_ref
            best_a = a
            best_b = b
        }
    }

    if (best_support > 0 && best_right > 0) {
        pos_mod = ((best_right - 1) % ref_len) + 1
        orientation = seg_strand[best_a] "/" seg_strand[best_b]
        print curr_read, seg_count, best_left, best_right, pos_mod, best_gap, best_support, orientation, "single_ref_adjacent_split"
    }

    clear_segments()
}

{
    read_id = $1
    flag = $2 + 0
    mapq = $5 + 0
    cigar = $6
    pos = $4 + 0
    if (read_id != curr_read) {
        flush_read()
        curr_read = read_id
        seg_count = 0
    }
    if (cigar == "*" || pos <= 0 || mapq < min_mapq) next

    rspan = ref_span(cigar)
    if (rspan <= 0) next
    qspan = query_span(cigar)
    if (qspan < min_seg_bp) next

    lead = leading_clip(cigar)
    trail = trailing_clip(cigar)
    is_rev = (int(flag / 16) % 2 == 1)
    qstart = is_rev ? (trail + 1) : (lead + 1)
    qend = is_rev ? (trail + qspan) : (lead + qspan)

    seg_count++
    seg_qstart[seg_count] = qstart
    seg_qend[seg_count] = qend
    seg_rstart[seg_count] = pos
    seg_rend[seg_count] = pos + rspan - 1
    seg_span[seg_count] = qspan
    seg_strand[seg_count] = is_rev ? "-" : "+"
}

END {
    flush_read()
}
