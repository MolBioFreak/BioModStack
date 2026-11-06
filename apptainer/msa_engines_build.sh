#!/bin/sh
# Run inside the pinned Ubuntu base, not on the host.
set -eu
export LC_ALL=C
mkdir -p /opt/msa/bin /opt/msa/share/provenance /opt/msa/share/licenses /root/msa-build
cd /root/msa-build
prov=/opt/msa/share/provenance
trap 'for f in "$prov"/*build.log "$prov"/*configure.log; do if test -f "$f"; then printf "\n--- %s ---\n" "$f"; tail -n 30 "$f"; fi; done' EXIT
curl -fsSL https://mafft.cbrc.jp/alignment/software/mafft-7.525-without-extensions-src.tgz -o mafft.tgz
printf '%s  %s\n' edb34ae9b26d6b55328c18fa060ed741bca8cd599c2f4f8fad0e0871c8082265 mafft.tgz | sha256sum -c -
tar xzf mafft.tgz
clone() {
    git clone -q --no-checkout "$2" "$1"
    git -C "$1" checkout -q "$3"
    test "$(git -C "$1" rev-parse HEAD)" = "$3"
    git -C "$1" submodule update --init --recursive
    git -C "$1" submodule status --recursive > "$prov/$1-submodules.txt"
}
clone famsa https://github.com/refresh-bio/FAMSA.git 259841074f86361b50367bed396b4e270dfe343a
clone muscle https://github.com/rcedgar/muscle.git 2cf9d33078c9a85697e38a6f3ad827e6862420df
clone twilight https://github.com/TurakhiaLab/TWILIGHT.git 4839e073048c2f60f2949a8342b6dc0a704ddbed
curl -fsSL https://raw.githubusercontent.com/rcedgar/vcxproj_make/806d016/vcxproj_make.py -o vcxproj_make.py
printf '%s  %s\n' 902735703004c47705ffa389329378f237fecb154945b489edf6abe260c6694f vcxproj_make.py | sha256sum -c -
cp mafft-7.525-without-extensions/license /opt/msa/share/licenses/mafft.txt
cp famsa/LICENSE /opt/msa/share/licenses/famsa.txt
cp muscle/LICENSE /opt/msa/share/licenses/muscle.txt
cp twilight/LICENSE /opt/msa/share/licenses/twilight.txt
for dep in isa-l libdeflate mimalloc zlib-ng; do mkdir -p "/opt/msa/share/licenses/famsa-$dep"; find "famsa/libs/$dep" -maxdepth 2 -type f \( -iname 'license*' -o -iname 'copying*' -o -iname 'notice*' \) -exec cp --parents {} "/opt/msa/share/licenses/famsa-$dep/" \;; done
( cd mafft-7.525-without-extensions/core; make -j2 PREFIX=/opt/msa; make PREFIX=/opt/msa install ) > "$prov/mafft-build.log" 2>&1
( cd famsa; make -j2 PLATFORM=generic ) > "$prov/famsa-build.log" 2>&1
cp famsa/bin/famsa /opt/msa/bin/
python3 -c 'from pathlib import Path; p=Path("vcxproj_make.py"); s=p.read_text(); assert s.count("-march=native") == 2; p.write_text(s.replace("-march=native", "-march=x86-64"))'
( cd muscle/src; python3 /root/msa-build/vcxproj_make.py --openmp ) > "$prov/muscle-build.log" 2>&1
cp muscle/bin/muscle /opt/msa/bin/
# Upstream OFF flags alone still auto-detect. Disable both probes and remove
# native architecture flags from the pinned source copy, not the checkout.
python3 -c 'from pathlib import Path; p=Path("twilight/CMakeLists.txt"); s=p.read_text(); assert s.count("-march=native") == 5 and s.count("add_compile_definitions(TALCO_SIMD)") == 1; p.write_text(s.replace("-march=native", "-march=x86-64").replace("add_compile_definitions(TALCO_SIMD)", "# Generic CPU: use upstream scalar TALCO"))'
cmake -S twilight -B twilight/build -DUSE_CUDA=OFF -DUSE_HIP=OFF -DCMAKE_DISABLE_FIND_PACKAGE_HIP=TRUE -DCMAKE_CUDA_COMPILER=NOTFOUND -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > "$prov/twilight-configure.log" 2>&1
cmake --build twilight/build -j2 > "$prov/twilight-build.log" 2>&1
cp twilight/bin/twilight /opt/msa/bin/
cp twilight/build/compile_commands.json "$prov/twilight-compile-commands.json"
dpkg-query -W > "$prov/debian-packages.txt"
{ gcc --version; g++ --version; cmake --version; } > "$prov/toolchain.txt"
sha256sum /opt/msa/bin/* /opt/msa/libexec/mafft/* > "$prov/executable-sha256.txt"
ldd /opt/msa/bin/twilight > "$prov/twilight-ldd.txt"
rm -rf /root/msa-build
