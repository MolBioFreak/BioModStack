import React, { useState, useRef, useEffect } from "react";
import { Tag, Intent, Button } from "@blueprintjs/core";

/**
 * PrimerSequenceInput - Optimized primer binding site detector
 * Debounced search to reduce RAM usage and lag
 */
export default function PrimerSequenceInput({
    readOnly,
    sequenceData,
    change
}) {
    const [primerSeq, setPrimerSeq] = useState("");
    const [results, setResults] = useState([]);
    const [selected, setSelected] = useState(0);
    const timerRef = useRef(null);

    const plasmid = (sequenceData?.sequence || "").toUpperCase();
    const isCircular = sequenceData?.circular ?? false;

    // Inline reverse complement
    const revComp = (s) => {
        const c = { A: "T", T: "A", C: "G", G: "C", N: "N" };
        return s.split("").reverse().map(x => c[x] || x).join("");
    };

    // Find all occurrences - optimized
    const findAll = (hay, needle) => {
        const r = [];
        let p = hay.indexOf(needle);
        while (p !== -1) {
            r.push(p);
            p = hay.indexOf(needle, p + 1);
        }
        return r;
    };

    // Debounced search
    useEffect(() => {
        if (timerRef.current) clearTimeout(timerRef.current);

        if (!primerSeq || primerSeq.length < 6 || !plasmid) {
            setResults([]);
            return;
        }

        timerRef.current = setTimeout(() => {
            const s = primerSeq;
            const hay = isCircular ? plasmid + plasmid : plasmid;
            const len = plasmid.length;

            // Forward strand
            const fwd = findAll(hay, s)
                .filter(p => p < len)
                .map(p => ({ start: p, end: p + s.length - 1, fwd: true }));

            // Reverse complement
            const rc = revComp(s);
            const rev = findAll(hay, rc)
                .filter(p => p < len)
                .map(p => ({ start: p, end: p + rc.length - 1, fwd: false }));

            const all = [...fwd, ...rev].sort((a, b) => a.start - b.start);
            setResults(all);
            setSelected(0);

            // Auto-select first match
            if (all.length > 0) {
                const m = all[0];
                change("start", m.start + 1);
                change("end", m.end + 1);
                change("forward", m.fwd);
            }
        }, 300);

        return () => {
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, [primerSeq, plasmid, isCircular]);

    const handleChange = (e) => {
        const v = e.target.value.toUpperCase().replace(/[^ATCGN]/g, "");
        setPrimerSeq(v);
    };

    const handleFlip = () => {
        setPrimerSeq(revComp(primerSeq));
    };

    const handleClick = (i) => {
        setSelected(i);
        const m = results[i];
        if (m) {
            change("start", m.start + 1);
            change("end", m.end + 1);
            change("forward", m.fwd);
        }
    };

    const sel = results[selected];
    const matchSeq = sel ? plasmid.slice(sel.start, sel.end + 1) : "";

    return (
        <div style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <label style={{ minWidth: 90, fontWeight: 500 }}>Primer</label>
                <input
                    type="text"
                    className="bp3-input"
                    style={{ fontFamily: "monospace", flex: 1, maxWidth: 260 }}
                    placeholder="Enter sequence (min 6bp)"
                    value={primerSeq}
                    onChange={handleChange}
                    disabled={readOnly}
                />
                {primerSeq.length > 0 && (
                    <>
                        <span className="bp3-text-muted">{primerSeq.length}bp</span>
                        <Button small minimal icon="swap-horizontal" title="Rev-comp" onClick={handleFlip} disabled={readOnly} />
                    </>
                )}
            </div>

            {primerSeq.length >= 6 && results.length > 0 && (
                <div style={{ marginLeft: 100 }}>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center", marginBottom: 6 }}>
                        <Tag intent={Intent.SUCCESS} minimal>{results.length} site{results.length > 1 ? "s" : ""}</Tag>
                        {results.slice(0, 6).map((m, i) => (
                            <Tag
                                key={`${m.start}-${m.fwd}`}
                                interactive
                                intent={i === selected ? Intent.PRIMARY : Intent.NONE}
                                onClick={() => handleClick(i)}
                                style={{ cursor: "pointer" }}
                            >
                                {m.start + 1}–{m.end + 1} {m.fwd ? "(+)" : "(−)"}
                            </Tag>
                        ))}
                    </div>
                    {sel && (
                        <div style={{ padding: "4px 8px", background: "#f5f5f5", borderRadius: 3, fontSize: 11 }}>
                            <span style={{ color: "#666" }}>5'–</span>
                            <span style={{ fontFamily: "monospace", fontWeight: 600 }}>{matchSeq}</span>
                            <span style={{ color: "#666" }}>–3' ({sel.fwd ? "+" : "−"})</span>
                        </div>
                    )}
                </div>
            )}

            {primerSeq.length >= 6 && results.length === 0 && (
                <div style={{ marginLeft: 100 }}>
                    <Tag intent={Intent.WARNING} minimal>No binding sites</Tag>
                </div>
            )}
        </div>
    );
}
