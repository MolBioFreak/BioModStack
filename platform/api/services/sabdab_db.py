"""
SAbDab Local Database - SQLite-backed VHH structure database.

Provides offline-capable search over locally mirrored SAbDab VHH data
with pre-computed CDR-H3 lengths.

License: CC-BY 4.0 (https://creativecommons.org/licenses/by/4.0/)
Attribution: Schneider, C. et al. (2022) Nucleic Acids Res. 50(D1):D1368-D1372
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from contextlib import contextmanager

from paths import get_sabdab_cache_dir

logger = logging.getLogger(__name__)


def get_sabdab_db_path() -> Path:
    """Get the path to the local SAbDab SQLite database."""
    return get_sabdab_cache_dir() / "sabdab_vhh.db"


# Schema version for migrations
SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- SAbDab VHH structures local mirror
-- Schema version: 1
-- CDR lengths computed from IMGT annotations

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vhh_structures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pdb_code TEXT NOT NULL,
    h_chain TEXT NOT NULL,
    model INTEGER DEFAULT 0,
    
    -- Structure metadata
    resolution REAL,
    method TEXT,
    r_free REAL,
    r_factor REAL,
    date TEXT,
    
    -- Antibody properties
    heavy_species TEXT,
    heavy_subclass TEXT,
    engineered INTEGER DEFAULT 0,
    scfv INTEGER DEFAULT 0,
    
    -- Antigen info
    antigen_chain TEXT,
    antigen_type TEXT,
    antigen_name TEXT,
    antigen_species TEXT,
    
    -- CDR data (pre-computed from IMGT)
    cdr_h1_length INTEGER,
    cdr_h2_length INTEGER,
    cdr_h3_length INTEGER,
    cdr_h3_sequence TEXT,
    
    -- Affinity data
    affinity REAL,
    delta_g REAL,
    affinity_method TEXT,
    
    -- Provenance
    pmid TEXT,
    authors TEXT,
    
    -- Sync metadata
    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(pdb_code, h_chain, model)
);

CREATE INDEX IF NOT EXISTS idx_resolution ON vhh_structures(resolution);
CREATE INDEX IF NOT EXISTS idx_species ON vhh_structures(heavy_species);
CREATE INDEX IF NOT EXISTS idx_germline ON vhh_structures(heavy_subclass);
CREATE INDEX IF NOT EXISTS idx_antigen_type ON vhh_structures(antigen_type);
CREATE INDEX IF NOT EXISTS idx_cdr_h3_length ON vhh_structures(cdr_h3_length);
CREATE INDEX IF NOT EXISTS idx_method ON vhh_structures(method);
CREATE INDEX IF NOT EXISTS idx_pdb_code ON vhh_structures(pdb_code);

-- Sync log for tracking updates
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type TEXT NOT NULL,  -- 'initial' or 'incremental'
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    entries_added INTEGER DEFAULT 0,
    entries_updated INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    error_message TEXT
);
"""


@dataclass
class VHHStructure:
    """A VHH structure entry from the local database."""
    pdb_code: str
    h_chain: str
    model: int = 0
    resolution: Optional[float] = None
    method: Optional[str] = None
    r_free: Optional[float] = None
    r_factor: Optional[float] = None
    date: Optional[str] = None
    heavy_species: Optional[str] = None
    heavy_subclass: Optional[str] = None
    engineered: bool = False
    scfv: bool = False
    antigen_chain: Optional[str] = None
    antigen_type: Optional[str] = None
    antigen_name: Optional[str] = None
    antigen_species: Optional[str] = None
    cdr_h1_length: Optional[int] = None
    cdr_h2_length: Optional[int] = None
    cdr_h3_length: Optional[int] = None
    cdr_h3_sequence: Optional[str] = None
    affinity: Optional[float] = None
    delta_g: Optional[float] = None
    affinity_method: Optional[str] = None
    pmid: Optional[str] = None
    authors: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return asdict(self)


class SAbDabDatabase:
    """SQLite-backed local mirror of SAbDab VHH structures."""
    
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_sabdab_db_path()
        self._ensure_schema()
    
    @contextmanager
    def _connection(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def _ensure_schema(self):
        """Create database schema if it doesn't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.executescript(SCHEMA_SQL)
            
            # Check/set schema version
            cursor.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
            row = cursor.fetchone()
            if row is None:
                cursor.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()
        
        logger.info(f"[SAbDab DB] Initialized at {self.db_path}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # Total entries
            cursor.execute("SELECT COUNT(*) FROM vhh_structures")
            total = cursor.fetchone()[0]
            
            # Entries with CDR-H3 length
            cursor.execute("SELECT COUNT(*) FROM vhh_structures WHERE cdr_h3_length IS NOT NULL")
            with_cdr = cursor.fetchone()[0]
            
            # Last sync
            cursor.execute("""
                SELECT completed_at, entries_added, status 
                FROM sync_log 
                WHERE status = 'completed'
                ORDER BY completed_at DESC LIMIT 1
            """)
            last_sync = cursor.fetchone()
            
            # Species distribution
            cursor.execute("""
                SELECT heavy_species, COUNT(*) as count 
                FROM vhh_structures 
                WHERE heavy_species IS NOT NULL
                GROUP BY heavy_species 
                ORDER BY count DESC 
                LIMIT 10
            """)
            species = {row["heavy_species"]: row["count"] for row in cursor.fetchall()}
            
            return {
                "total_entries": total,
                "entries_with_cdr_h3": with_cdr,
                "last_sync": last_sync["completed_at"] if last_sync else None,
                "species_distribution": species,
                "db_path": str(self.db_path),
                "db_size_mb": round(self.db_path.stat().st_size / 1024 / 1024, 2) if self.db_path.exists() else 0
            }
    
    def search(
        self,
        species: Optional[str] = None,
        resolution_min: Optional[float] = None,
        resolution_max: Optional[float] = None,
        cdr_h3_min: Optional[int] = None,
        cdr_h3_max: Optional[int] = None,
        antigen_type: Optional[str] = None,
        has_antigen: Optional[bool] = None,
        methods: Optional[List[str]] = None,
        germlines: Optional[List[str]] = None,
        has_affinity: Optional[bool] = None,
        include_scfv: bool = False,
        sort_by: str = "resolution",
        sort_desc: bool = False,
        limit: int = 100,
        offset: int = 0
    ) -> List[VHHStructure]:
        """
        Search the local VHH database with comprehensive filters.
        
        Args:
            species: Filter by species (substring match, case-insensitive)
            resolution_min: Minimum resolution in Angstroms
            resolution_max: Maximum resolution in Angstroms
            cdr_h3_min: Minimum CDR-H3 length
            cdr_h3_max: Maximum CDR-H3 length
            antigen_type: Filter by antigen type (e.g., 'protein', 'peptide')
            has_antigen: True = bound structures only, False = unbound only
            methods: List of experimental methods (e.g., ['X-RAY DIFFRACTION'])
            germlines: List of germline subclasses (e.g., ['IGHV1', 'IGHV3'])
            has_affinity: True = structures with affinity data only
            include_scfv: Whether to include scFv structures (default: VHH only)
            sort_by: Sort field
            sort_desc: Sort descending
            limit: Maximum results
            offset: Pagination offset
        
        Returns:
            List of VHHStructure objects
        """
        conditions = []
        params = []
        
        # Exclude scFv unless explicitly included
        if not include_scfv:
            conditions.append("scfv = 0")
        
        # Species filter (case-insensitive substring)
        if species:
            conditions.append("LOWER(heavy_species) LIKE LOWER(?)")
            params.append(f"%{species}%")
        
        # Resolution range
        if resolution_min is not None:
            conditions.append("resolution >= ?")
            params.append(resolution_min)
        if resolution_max is not None:
            conditions.append("(resolution IS NULL OR resolution <= ?)")
            params.append(resolution_max)
        
        # CDR-H3 length range
        if cdr_h3_min is not None:
            conditions.append("cdr_h3_length >= ?")
            params.append(cdr_h3_min)
        if cdr_h3_max is not None:
            conditions.append("cdr_h3_length <= ?")
            params.append(cdr_h3_max)
        
        # Antigen type
        if antigen_type:
            conditions.append("LOWER(antigen_type) LIKE LOWER(?)")
            params.append(f"%{antigen_type}%")
        
        # Has antigen (bound vs unbound)
        if has_antigen is True:
            conditions.append("antigen_chain IS NOT NULL")
        elif has_antigen is False:
            conditions.append("antigen_chain IS NULL")
        
        # Experimental methods
        if methods:
            placeholders = ",".join("?" * len(methods))
            conditions.append(f"UPPER(method) IN ({placeholders})")
            params.extend([m.upper() for m in methods])
        
        # Germlines
        if germlines:
            placeholders = ",".join("?" * len(germlines))
            conditions.append(f"UPPER(heavy_subclass) IN ({placeholders})")
            params.extend([g.upper() for g in germlines])
        
        # Has affinity data
        if has_affinity is True:
            conditions.append("affinity IS NOT NULL")
        
        # Build query
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Validate sort field
        valid_sort_fields = {
            "resolution", "cdr_h3_length", "pdb_code", "date", 
            "heavy_species", "antigen_type", "affinity"
        }
        if sort_by not in valid_sort_fields:
            sort_by = "resolution"
        
        order_dir = "DESC" if sort_desc else "ASC"
        
        # Handle NULL sorting (NULLs last for ASC, first for DESC)
        null_order = "NULLS LAST" if not sort_desc else "NULLS FIRST"
        
        query = f"""
            SELECT * FROM vhh_structures
            WHERE {where_clause}
            ORDER BY {sort_by} {order_dir} {null_order}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
        
        results = []
        for row in rows:
            results.append(VHHStructure(
                pdb_code=row["pdb_code"],
                h_chain=row["h_chain"],
                model=row["model"],
                resolution=row["resolution"],
                method=row["method"],
                r_free=row["r_free"],
                r_factor=row["r_factor"],
                date=row["date"],
                heavy_species=row["heavy_species"],
                heavy_subclass=row["heavy_subclass"],
                engineered=bool(row["engineered"]),
                scfv=bool(row["scfv"]),
                antigen_chain=row["antigen_chain"],
                antigen_type=row["antigen_type"],
                antigen_name=row["antigen_name"],
                antigen_species=row["antigen_species"],
                cdr_h1_length=row["cdr_h1_length"],
                cdr_h2_length=row["cdr_h2_length"],
                cdr_h3_length=row["cdr_h3_length"],
                cdr_h3_sequence=row["cdr_h3_sequence"],
                affinity=row["affinity"],
                delta_g=row["delta_g"],
                affinity_method=row["affinity_method"],
                pmid=row["pmid"],
                authors=row["authors"]
            ))
        
        logger.info(f"[SAbDab DB] Search returned {len(results)} results")
        return results
    
    def _build_where_clause(
        self,
        species: Optional[str] = None,
        resolution_min: Optional[float] = None,
        resolution_max: Optional[float] = None,
        cdr_h3_min: Optional[int] = None,
        cdr_h3_max: Optional[int] = None,
        antigen_type: Optional[str] = None,
        has_antigen: Optional[bool] = None,
        methods: Optional[List[str]] = None,
        germlines: Optional[List[str]] = None,
        has_affinity: Optional[bool] = None,
        include_scfv: bool = False,
        pdb_code: Optional[str] = None,
    ) -> tuple:
        """Build WHERE clause and params for filters. Returns (where_clause, params)."""
        conditions = []
        params = []
        
        # Exclude scFv unless explicitly included
        if not include_scfv:
            conditions.append("scfv = 0")
        
        # PDB code filter (exact match)
        if pdb_code:
            conditions.append("LOWER(pdb_code) = LOWER(?)")
            params.append(pdb_code)
        
        # Species filter (case-insensitive substring)
        if species:
            conditions.append("LOWER(heavy_species) LIKE LOWER(?)")
            params.append(f"%{species}%")
        
        # Resolution range
        if resolution_min is not None:
            conditions.append("resolution >= ?")
            params.append(resolution_min)
        if resolution_max is not None:
            conditions.append("(resolution IS NULL OR resolution <= ?)")
            params.append(resolution_max)
        
        # CDR-H3 length range
        if cdr_h3_min is not None:
            conditions.append("cdr_h3_length >= ?")
            params.append(cdr_h3_min)
        if cdr_h3_max is not None:
            conditions.append("cdr_h3_length <= ?")
            params.append(cdr_h3_max)
        
        # Antigen type
        if antigen_type:
            conditions.append("LOWER(antigen_type) LIKE LOWER(?)")
            params.append(f"%{antigen_type}%")
        
        # Has antigen (bound vs unbound)
        if has_antigen is True:
            conditions.append("antigen_chain IS NOT NULL")
        elif has_antigen is False:
            conditions.append("antigen_chain IS NULL")
        
        # Experimental methods
        if methods:
            placeholders = ",".join("?" * len(methods))
            conditions.append(f"UPPER(method) IN ({placeholders})")
            params.extend([m.upper() for m in methods])
        
        # Germlines
        if germlines:
            placeholders = ",".join("?" * len(germlines))
            conditions.append(f"UPPER(heavy_subclass) IN ({placeholders})")
            params.extend([g.upper() for g in germlines])
        
        # Has affinity data
        if has_affinity is True:
            conditions.append("affinity IS NOT NULL")
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        return where_clause, params
    
    def count(
        self,
        species: Optional[str] = None,
        resolution_min: Optional[float] = None,
        resolution_max: Optional[float] = None,
        cdr_h3_min: Optional[int] = None,
        cdr_h3_max: Optional[int] = None,
        antigen_type: Optional[str] = None,
        has_antigen: Optional[bool] = None,
        methods: Optional[List[str]] = None,
        germlines: Optional[List[str]] = None,
        has_affinity: Optional[bool] = None,
        include_scfv: bool = False,
    ) -> int:
        """Count entries matching filters."""
        where_clause, params = self._build_where_clause(
            species=species,
            resolution_min=resolution_min,
            resolution_max=resolution_max,
            cdr_h3_min=cdr_h3_min,
            cdr_h3_max=cdr_h3_max,
            antigen_type=antigen_type,
            has_antigen=has_antigen,
            methods=methods,
            germlines=germlines,
            has_affinity=has_affinity,
            include_scfv=include_scfv,
        )
        
        query = f"SELECT COUNT(*) FROM vhh_structures WHERE {where_clause}"
        
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone()[0]
    
    def get_by_pdb(self, pdb_code: str) -> List[VHHStructure]:
        """Get all entries for a specific PDB code."""
        where_clause, params = self._build_where_clause(pdb_code=pdb_code, include_scfv=True)
        
        query = f"SELECT * FROM vhh_structures WHERE {where_clause}"
        
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
        
        results = []
        for row in rows:
            results.append(VHHStructure(
                pdb_code=row["pdb_code"],
                h_chain=row["h_chain"],
                model=row["model"],
                resolution=row["resolution"],
                method=row["method"],
                r_free=row["r_free"],
                r_factor=row["r_factor"],
                date=row["date"],
                heavy_species=row["heavy_species"],
                heavy_subclass=row["heavy_subclass"],
                engineered=bool(row["engineered"]),
                scfv=bool(row["scfv"]),
                antigen_chain=row["antigen_chain"],
                antigen_type=row["antigen_type"],
                antigen_name=row["antigen_name"],
                antigen_species=row["antigen_species"],
                cdr_h1_length=row["cdr_h1_length"],
                cdr_h2_length=row["cdr_h2_length"],
                cdr_h3_length=row["cdr_h3_length"],
                cdr_h3_sequence=row["cdr_h3_sequence"],
                affinity=row["affinity"],
                delta_g=row["delta_g"],
                affinity_method=row["affinity_method"],
                pmid=row["pmid"],
                authors=row["authors"]
            ))
        return results
    
    def upsert(self, entry: VHHStructure) -> bool:
        """Insert or update a VHH structure entry."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO vhh_structures (
                    pdb_code, h_chain, model, resolution, method, r_free, r_factor,
                    date, heavy_species, heavy_subclass, engineered, scfv,
                    antigen_chain, antigen_type, antigen_name, antigen_species,
                    cdr_h1_length, cdr_h2_length, cdr_h3_length, cdr_h3_sequence,
                    affinity, delta_g, affinity_method, pmid, authors, last_synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(pdb_code, h_chain, model) DO UPDATE SET
                    resolution = excluded.resolution,
                    method = excluded.method,
                    r_free = excluded.r_free,
                    r_factor = excluded.r_factor,
                    date = excluded.date,
                    heavy_species = excluded.heavy_species,
                    heavy_subclass = excluded.heavy_subclass,
                    engineered = excluded.engineered,
                    scfv = excluded.scfv,
                    antigen_chain = excluded.antigen_chain,
                    antigen_type = excluded.antigen_type,
                    antigen_name = excluded.antigen_name,
                    antigen_species = excluded.antigen_species,
                    cdr_h1_length = excluded.cdr_h1_length,
                    cdr_h2_length = excluded.cdr_h2_length,
                    cdr_h3_length = excluded.cdr_h3_length,
                    cdr_h3_sequence = excluded.cdr_h3_sequence,
                    affinity = excluded.affinity,
                    delta_g = excluded.delta_g,
                    affinity_method = excluded.affinity_method,
                    pmid = excluded.pmid,
                    authors = excluded.authors,
                    last_synced = CURRENT_TIMESTAMP
            """, (
                entry.pdb_code, entry.h_chain, entry.model, entry.resolution,
                entry.method, entry.r_free, entry.r_factor, entry.date,
                entry.heavy_species, entry.heavy_subclass, 
                1 if entry.engineered else 0, 1 if entry.scfv else 0,
                entry.antigen_chain, entry.antigen_type, entry.antigen_name,
                entry.antigen_species, entry.cdr_h1_length, entry.cdr_h2_length,
                entry.cdr_h3_length, entry.cdr_h3_sequence, entry.affinity,
                entry.delta_g, entry.affinity_method, entry.pmid, entry.authors
            ))
            conn.commit()
            return cursor.rowcount > 0
    
    def start_sync_log(self, sync_type: str = "incremental") -> int:
        """Start a sync log entry, returns log ID."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sync_log (sync_type, started_at, status)
                VALUES (?, CURRENT_TIMESTAMP, 'running')
            """, (sync_type,))
            conn.commit()
            return cursor.lastrowid
    
    def complete_sync_log(self, log_id: int, entries_added: int, entries_updated: int = 0, error: Optional[str] = None):
        """Complete a sync log entry."""
        status = "failed" if error else "completed"
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sync_log 
                SET completed_at = CURRENT_TIMESTAMP,
                    entries_added = ?,
                    entries_updated = ?,
                    status = ?,
                    error_message = ?
                WHERE id = ?
            """, (entries_added, entries_updated, status, error, log_id))
            conn.commit()
    
    def get_existing_pdb_codes(self) -> set:
        """Get set of all PDB codes in the database."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT pdb_code FROM vhh_structures")
            return {row["pdb_code"] for row in cursor.fetchall()}
    
    def get_filter_options(self) -> Dict[str, List[str]]:
        """Get available filter options for UI dropdowns."""
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # Species
            cursor.execute("""
                SELECT DISTINCT heavy_species FROM vhh_structures 
                WHERE heavy_species IS NOT NULL 
                ORDER BY heavy_species
            """)
            species = [row["heavy_species"] for row in cursor.fetchall()]
            
            # Methods
            cursor.execute("""
                SELECT DISTINCT method FROM vhh_structures 
                WHERE method IS NOT NULL 
                ORDER BY method
            """)
            methods = [row["method"] for row in cursor.fetchall()]
            
            # Antigen types
            cursor.execute("""
                SELECT DISTINCT antigen_type FROM vhh_structures 
                WHERE antigen_type IS NOT NULL 
                ORDER BY antigen_type
            """)
            antigen_types = [row["antigen_type"] for row in cursor.fetchall()]
            
            # Germlines
            cursor.execute("""
                SELECT DISTINCT heavy_subclass FROM vhh_structures 
                WHERE heavy_subclass IS NOT NULL 
                ORDER BY heavy_subclass
            """)
            germlines = [row["heavy_subclass"] for row in cursor.fetchall()]
            
            # CDR-H3 length range
            cursor.execute("""
                SELECT MIN(cdr_h3_length) as min_len, MAX(cdr_h3_length) as max_len
                FROM vhh_structures WHERE cdr_h3_length IS NOT NULL
            """)
            cdr_range = cursor.fetchone()
            
            return {
                "species": species,
                "methods": methods,
                "antigen_types": antigen_types,
                "germlines": germlines,
                "cdr_h3_length_range": [cdr_range["min_len"], cdr_range["max_len"]] if cdr_range["min_len"] else [5, 25]
            }


# Module-level singleton
_db_instance: Optional[SAbDabDatabase] = None


def get_sabdab_db() -> SAbDabDatabase:
    """Get the singleton database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = SAbDabDatabase()
    return _db_instance
