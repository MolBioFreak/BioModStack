import sys

print("Testing ANARCII import...")
try:
    from ANARCII import Anarcii
    print("Import 'from ANARCII import Anarcii' successful")
except ImportError:
    print("Import 'from ANARCII import Anarcii' failed")
    try:
        import ANARCII
        print("Import 'import ANARCII' successful")
        print(dir(ANARCII))
    except ImportError:
        try:
             import anarcii
             from anarcii import Anarcii
             print("Import 'import anarcii' successful")
             print(f"dir(anarcii): {dir(anarcii)}")
             print(f"dir(Anarcii): {dir(Anarcii)}")
             
        except ImportError as e:
             print(f"All imports failed: {e}")
             sys.exit(1)

# Test usage
seq = "QVQLQESGPGLVKPSETLSLTCTVSGGSVSSGSYYWSWIRQPPGKGLEWIGYIYYSGSTNYNPSLKSRVTISVDTSKNQFSLKLSSVTAADTAVYYCAR"
print(f"\nTesting run_anarcii with sequence length {len(seq)}...")

try:
    runner = Anarcii()
    print("Anarcii instantiated")
    print(f"dir(runner): {dir(runner)}")
    
    # helper to print structure of result
    def print_res(res):
        print(f"Result type: {type(res)}")
        if isinstance(res, (list, tuple)) and len(res) > 0:
             print(f"First element type: {type(res[0])}")
             print(f"First element: {res[0]}")

    # Attempt 3: runner.number([seq])
    print("\nAttempt 3: runner.number([seq])")
    try:
         res = runner.number([seq])
         print("Success number([seq])")
         print_res(res)
         if isinstance(res, dict):
             print(f"Result keys: {res.keys()}")
         
         # Attempt conversion to legacy format (no args?)
         try:
             legacy = runner.to_legacy()
             print("Success to_legacy()")
             print_res(legacy)
             if isinstance(legacy, tuple) and len(legacy) == 3:
                 print("Legacy format matches (numbering, alignments, hit_tables)!")
         except Exception as e:
             print(f"Failed to_legacy(): {e}")

    except Exception as e:
         print(f"Failed number([seq]): {e}")
         
         # Try list of tuples for number
         print("\nAttempt 4: runner.number([('seq1', seq)])")
         try:
             res = runner.number([("seq1", seq)])
             print("Success number([('seq1', seq)])")
             print_res(res)
         except Exception as e2:
             print(f"Failed number([tuple]): {e2}")

except Exception as e:
    print(f"Fatal error during testing: {e}")
