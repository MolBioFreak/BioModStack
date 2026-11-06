# Native Amber fd_helix wrapper notices

The BMS launcher and build script are Copyright (C) 2026 BMS contributors,
licensed GPL-3.0-or-later, without any warranty. The generated bundle includes
AmberTools' full LICENSE as `source/COPYING`; retain it with every copy.

AmberTools24.8 was distributed by conda-forge under
`GPL-3.0-or-later AND LGPL-3.0-or-later AND BSD-3-Clause AND MIT`.
The managed package's default is GPLv3 except its enumerated exceptions.
The isolated executable links only `fd_helix.c.o` from libnabc.a and system
libc/libm. The original fd_helix.c contains David A. Case's 2018 BSD-style
notice: retain that notice verbatim in `source/fd_helix.c`. Do not infer that
the entire AmberTools library is BSD from this individual source's notice.

The build bundles the complete linked routine's preferred source, original
interface headers, wrapper, build recipe, full installed license, compiler
identity, flags, and native/source differential receipts. No Amber source is
modified. Original upstream source archive:
https://ambermd.org/downloads/AmberTools24_rc5.tar.bz2
SHA256 52fb4fb3370a89b7ce738a2dc3e513c2fc1943fde4b4381846d9e75cc48d840f.
The source archive is retained alongside the local qualification bundle.
Never redistribute an isolated executable without its corresponding sources
and notices. Static glibc/libm retain LGPL-2.1-or-later terms and the GCC runtime
retains its runtime exception; their installed copyright notices and LGPL text
are included in `source/`. Relink to replacement system libraries with:

```
gcc -O2 -static -Wl,--build-id=none -I source/include \
  source/amber_fd_helix.c fd_helix.c.o -lm -o amber-fd-helix-relinked
```

Or compile `source/fd_helix.c` instead of `fd_helix.c.o` to rebuild all non-system
native code from preferred source (the build checks exact coordinate stdout).
Reproducing identical executable hashes requires the recorded compiler/linker
and system archive identities; equivalent coordinates are checked separately.
Packaging this subprocess does not assert a license change for
unrelated BMS code. Coordinate output is scientific data, not source code.

No inference, force field, minimization, protonation, folding, or bound-pose
prediction is performed. This is native fiber-diffraction idealized geometry.
