/** Workflow-owned analysis only. Never derives design authority from pSCE chains,
 * probability width, fixed masks, or child defaults. Admission owns params.
 */
class FampnnAnalysisPolicy {
    static def declarationValue(params) {
        def value = params.get('fampnn_analysis_declaration')
        def path = params.get('fampnn_analysis_declaration_path')
        def expected = params.get('fampnn_analysis_declaration_sha256')
        if (path != null || expected != null) {
            if (value != null || !(path instanceof CharSequence) || !(expected instanceof CharSequence) || !(expected ==~ /[0-9a-f]{64}/)) {
                throw new IllegalArgumentException('Conflicting or incomplete FA-MPNN declaration transport')
            }
            byte[] bytes = java.nio.file.Files.readAllBytes(java.nio.file.Path.of(path.toString()))
            def actual = java.security.MessageDigest.getInstance('SHA-256').digest(bytes).encodeHex().toString()
            if (actual != expected) throw new IllegalArgumentException('FA-MPNN declaration transport hash mismatch')
            return new groovy.json.JsonSlurper().parseText(new String(bytes, 'UTF-8'))
        }
        return value instanceof CharSequence ? new groovy.json.JsonSlurper().parseText(value.toString()) : value
    }

    static def stagePrepared(params, pdbs) {
        if (declarationValue(params) == null) return pdbs
        def files = (pdbs instanceof Collection ? pdbs : [pdbs]).flatten()
        return files.collectMany { path ->
            def receipt = path.resolveSibling(path.fileName.toString().replaceFirst(/\.pdb$/, '.fampnn_prep.json'))
            if (!java.nio.file.Files.isRegularFile(receipt)) {
                throw new IllegalArgumentException('Missing PrepFAMPNN transformation provenance')
            }
            [path, receipt]
        }
    }

    static Map forWorkflow(params, String owner, String declaration) {
        def marker = params.get('core_protein_scientific_contract')
        if (marker == null) return [:]  // Historical attempts remain historical.
        if (marker != 1 || marker instanceof Boolean) {
            throw new IllegalArgumentException('Unsupported core_protein_scientific_contract')
        }
        def policy = params.get('fampnn_analysis_policy')
        if (!(policy instanceof Map) || policy.owner != owner || policy.declaration != declaration) {
            def requestDeclaration = declarationValue(params)
            if (policy == null && requestDeclaration instanceof Map && requestDeclaration.owner == owner && requestDeclaration.declaration == declaration && requestDeclaration.schema_version == 1 && requestDeclaration.version == 1) {
                return [core_protein_scientific_contract: 1, declaration: requestDeclaration]
            }
            throw new IllegalArgumentException('FA-MPNN requires matching workflow-owned analysis policy; authority unresolved')
        }
        if (policy.schema_version != 1 || policy.version != 1 || !(policy.inputs instanceof Map) || policy.inputs.isEmpty()) {
            throw new IllegalArgumentException('FA-MPNN resolved policy inputs/version required')
        }
        return [core_protein_scientific_contract: 1, policy: policy]
    }

    static Map forChild(params) {
        if (params.get('core_protein_scientific_contract') == null) return [:]
        def policy = declarationValue(params) ?: params.get('fampnn_analysis_policy')
        def declarations = [
            protein_design: ['binder_role_residues', 'declared_protein_inputs'],
            antibody_denovo: ['authorized_sequence_design_region'],
            protein_local_redesign: ['sequence_redesign_positions_spec']
        ]
        if (!(policy instanceof Map) || !declarations.get(policy.owner, []).contains(policy.declaration)) {
            throw new IllegalArgumentException('FA-MPNN child must inherit parent analysis authority; no child default')
        }
        return forWorkflow(params, policy.owner.toString(), policy.declaration.toString())
    }
}
