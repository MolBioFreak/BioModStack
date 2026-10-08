# Retired Stats Toolkit ownership designs

> **Historical / superseded:** This directory records former BioModStack-owned assay, database-service, and Stats Toolkit designs. It is not active architecture or implementation guidance.

These documents are **historical and superseded**. They describe BioModStack core ownership of assay analytics, analytical PostgreSQL, Stats-tools services, or transitional extraction designs that P1 retired.

They remain available only as design history. They are **not active architecture, implementation guidance, deployment instructions, or compatibility contracts**.

Current boundary:

- BioModStack core does not own assay-specific qPCR, chromatography/HPLC/Empower, plate-map, instrument-ingest, or statistical-workbench behavior.
- BioModStack core does not own an analytical PostgreSQL or DB-service lifecycle for those workloads.
- The standalone Stats Toolkit is isolated behind generic, deterministic API and renderer contracts; core does not proxy or embed it.
