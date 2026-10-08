from anarcii import Anarcii

seq = "QVQLQESGPGLVKPSETLSLTCTVSGGSVSSGSYYWSWIRQPPGKGLEWIGYIYYSGSTNYNPSLKSRVTISVDTSKNQFSLKLSSVTAADTAVYYCAR"
runner = Anarcii()
try:
    runner.number([seq])
    legacy = runner.to_legacy()
    numbering = legacy[0]
    domain = numbering[0][0] # First sequence, first domain
    
    print(f"Domain type: {type(domain)}")
    print(f"Domain len: {len(domain)}")
    print(f"Domain len: {len(domain)}")
    for i, elem in enumerate(domain):
        s_val = str(elem)
        if len(s_val) > 100: s_val = s_val[:100] + "..."
        print(f"[{i}] type={type(elem).__name__}, val={s_val}")

except Exception as e:
    print(f"Error: {e}")
