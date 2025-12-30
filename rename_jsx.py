import os
import re

def has_jsx(content):
    # Heuristics for JSX
    if re.search(r'return\s*\(\s*<', content): return True
    if re.search(r'<\w+', content) and re.search(r'/>|</\w+>', content): return True
    if 'import React' in content: return True
    return False

def rename_package(root_dir):
    print(f"Scanning {root_dir}...")
    renamed_count = 0
    for root, dirs, files in os.walk(root_dir):
        if 'node_modules' in root: continue
        
        for file in files:
            if file.endswith('.js'):
                path = os.path.join(root, file)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    if has_jsx(content):
                        new_path = path[:-3] + '.jsx'
                        print(f"Renaming {path} -> {new_path}")
                        os.rename(path, new_path)
                        renamed_count += 1
                except Exception as e:
                    print(f"Error processing {path}: {e}")
    print(f"Renamed {renamed_count} files in {root_dir}")

if __name__ == "__main__":
    rename_package("packages/ove-react19/src")
    rename_package("packages/ui/src")
