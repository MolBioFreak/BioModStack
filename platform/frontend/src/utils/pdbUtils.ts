/**
 * PDB Parsing Utilities
 * Extract chain information and sequences from PDB file content
 */

// Standard 3-letter to 1-letter amino acid mapping
const AA3TO1: Record<string, string> = {
    'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
    'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
    'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
    'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V',
    // Non-standard / modified
    'MSE': 'M', 'HYP': 'P', 'PYL': 'O', 'SEC': 'U',
};

export interface Residue {
    chainId: string;
    resNum: number;
    resName: string;  // 3-letter code
    aa: string;       // 1-letter code
}

export interface Chain {
    id: string;
    sequence: string;
    residues: Residue[];
    length: number;
}

export interface ParsedPDB {
    chains: Chain[];
    title?: string;
}

/**
 * Parse PDB content and extract chains with sequences
 */
export function parsePDB(pdbContent: string): ParsedPDB {
    const lines = pdbContent.split('\n');
    const chainMap = new Map<string, Residue[]>();
    const seenResidues = new Set<string>(); // Avoid duplicate entries for same residue
    let title = '';

    for (const line of lines) {
        // Extract title
        if (line.startsWith('TITLE')) {
            title += line.slice(10).trim() + ' ';
            continue;
        }

        // Parse ATOM records (not HETATM for standard residues)
        if (line.startsWith('ATOM  ')) {
            const resName = line.slice(17, 20).trim();
            const chainId = line.slice(21, 22).trim() || 'A';
            const resNum = parseInt(line.slice(22, 26).trim());

            // Skip if not a standard amino acid
            const aa = AA3TO1[resName];
            if (!aa) continue;

            // Create unique key to avoid duplicates
            const key = `${chainId}:${resNum}`;
            if (seenResidues.has(key)) continue;
            seenResidues.add(key);

            // Add to chain
            if (!chainMap.has(chainId)) {
                chainMap.set(chainId, []);
            }
            chainMap.get(chainId)!.push({
                chainId,
                resNum,
                resName,
                aa
            });
        }
    }

    // Convert to sorted chains
    const chains: Chain[] = [];
    for (const [id, residues] of chainMap.entries()) {
        // Sort by residue number
        residues.sort((a, b) => a.resNum - b.resNum);
        chains.push({
            id,
            sequence: residues.map(r => r.aa).join(''),
            residues,
            length: residues.length
        });
    }

    // Sort chains by ID
    chains.sort((a, b) => a.id.localeCompare(b.id));

    return {
        chains,
        title: title.trim() || undefined
    };
}

/**
 * Read PDB file and parse it
 */
export async function parsePDBFile(file: File): Promise<ParsedPDB> {
    const content = await file.text();
    return parsePDB(content);
}

/**
 * Format selected residues for backend (e.g., "A45,A46,B100")
 */
export function formatSelectedResidues(residues: Residue[]): string {
    return residues.map(r => `${r.chainId}${r.resNum}`).join(',');
}

/**
 * Parse residue string back to residue refs
 */
export function parseResidueString(str: string): Array<{ chain: string; resNum: number }> {
    const results: Array<{ chain: string; resNum: number }> = [];
    const parts = str.split(',').map(s => s.trim()).filter(Boolean);

    for (const part of parts) {
        // Match patterns like "A45", "B100", etc.
        const match = part.match(/^([A-Z])(\d+)$/);
        if (match) {
            results.push({ chain: match[1], resNum: parseInt(match[2]) });
        }
    }

    return results;
}
