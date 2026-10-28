# BNS Plasmid Development Toolkit

A focused BMS edition for plasmid development, molecular biology and ONT/NGS.

The installed workspaces are **Projects**, **Molecular Tools** and **NGS**. The
native Control Panel starts, stops and reports the managed local runtime.
MolBio sequence editing, annotation, restriction analysis, PCR and assembly use
the existing scientific contracts. ONT/NGS uses the shared job, reference,
artifact and provenance authorities.

The generic Job Launcher, generic Data Viewer, MSA server, protein/structure,
MD, BioXP and standalone Stats workspaces are excluded. Shared engine modules
remain where the retained components depend on them.

Use the **BNS Plasmid Development Toolkit** desktop or Applications entry to
start the installed runtime. MolBio and NGS also have direct desktop entries.
Setup uses locked Python/frontend manifests and the native supported service
manager. Preserve the installation's selected interpreter and dependency roots
when invoking its launcher; the Control Panel retains those selections across
service actions and reboots.

The local edition owns independent core, MolBio, NGS and experiment SQLite
stores. The Control Panel reports the selected runtime's stores and can back
up all four with SQLite's online backup API, including committed WAL pages.

This edition's published test branch is **`bns-plasmid-development-toolkit-test`**
in [MolBioFreak/BioModStack](https://github.com/MolBioFreak/BioModStack/tree/bns-plasmid-development-toolkit-test).
The local `bns-plasmid-development-toolkit` branch records the original ONT-platform
integration history. The full upstream BMS `test` and `main` branches remain separate.

```bash
git clone --branch bns-plasmid-development-toolkit-test --single-branch https://github.com/MolBioFreak/BioModStack.git BNS-Plasmid-Development-Toolkit
```

Native desktop, annotation and MinKNOW integration templates are versioned under
[`config/bns-native-installation`](config/bns-native-installation/README.md). Runtime identifiers, schemas
and database filenames retain BMS compatibility. See
[the local distribution contract](docs/Local_NGS_MolBio_Distribution.md) and
[the service guide](AGENTS.md).

A running viewer/API is not scientific execution qualification. Pinned NGS
images, workflow assets and tool identities must pass their native acceptance
checks before processing is considered qualified.


Current acceptance includes saved plasmid import/editing, Project attachment,
Export to Project with optional recorded revision/operation/edit history, and
persistence across a managed API restart. The intake forms still use the shared
BMS experiment vocabulary; simplifying them for client work orders, plasmid
identity confirmation and other ONT use cases is planned separately.
