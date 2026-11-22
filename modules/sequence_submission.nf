nextflow.enable.dsl = 2

def invalidSequenceBatchIdentity(message) {
    throw new IllegalArgumentException("protein_design:invalid_sequence_batch_identity:${message}")
}

def sequenceSubmissionMetadata(submissionId, submissionName, sequence, fold = null, rank = null) {
    def authority = submissionId?.toString()?.trim()
    def displayName = submissionName?.toString()?.trim()
    def sequenceValue = sequence?.toString()?.trim()
    if (!(authority ==~ /[A-Za-z0-9][A-Za-z0-9._-]*/)) {
        invalidSequenceBatchIdentity('entry ID must be non-empty and safe')
    }
    if (!displayName || !sequenceValue) {
        invalidSequenceBatchIdentity('entry name and sequence must be non-empty')
    }
    if (rank != null && (!(rank instanceof Integer) || (rank as int) < 0)) {
        invalidSequenceBatchIdentity('entry rank must be null or a non-negative integer')
    }
    if (fold != null && !(fold instanceof Integer) && !(fold instanceof CharSequence)) {
        invalidSequenceBatchIdentity('entry fold must be null, an integer, or a string')
    }
    return [
        producer_artifact_id: authority,
        producer_artifact_key: authority,
        producer_sample: authority,
        producer_sequence: sequenceValue,
        producer_fold: fold,
        producer_rank: rank,
        producer_submission_id: authority,
        producer_submission_name: displayName,
        original_submission_identity: [id: authority, name: displayName],
    ]
}

def normalizeProteinDesignSequenceBatchEntries(rawEntries) {
    if (!(rawEntries instanceof Collection) || rawEntries.isEmpty()) {
        invalidSequenceBatchIdentity('batch must contain at least one entry')
    }
    def normalized = rawEntries.collect { rawEntry ->
        if (!(rawEntry instanceof Map)) {
            invalidSequenceBatchIdentity('every batch entry must be an object')
        }
        def explicitIdentityFields = ['producer_artifact_id', 'entry_id', 'id'].findAll {
            rawEntry.containsKey(it)
        }
        def explicitValues = explicitIdentityFields.collect {
            rawEntry[it] == null ? '' : rawEntry[it].toString().trim()
        }
        if (explicitIdentityFields && (explicitValues.any { !it } || explicitValues.toSet().size() != 1)) {
            invalidSequenceBatchIdentity('explicit entry IDs must be non-empty and agree')
        }
        def legacyName = rawEntry.name == null ? '' : rawEntry.name.toString().trim()
        def authority = explicitValues ? explicitValues[0] : legacyName
        def sequence = rawEntry.sequence == null ? '' : rawEntry.sequence.toString().trim()
        def displayName = legacyName ?: authority
        def metadata = sequenceSubmissionMetadata(
            authority,
            displayName,
            sequence,
            rawEntry.containsKey('fold') ? rawEntry.fold : null,
            rawEntry.containsKey('rank') ? rawEntry.rank : null,
        )
        tuple(metadata, sequence, authority)
    }
    def artifactIds = normalized.collect { it[0].producer_artifact_id }
    def artifactKeys = normalized.collect { it[0].producer_artifact_key }
    if (artifactIds.toSet().size() != artifactIds.size() || artifactKeys.toSet().size() != artifactKeys.size()) {
        invalidSequenceBatchIdentity('entry IDs must be unique within the batch')
    }
    return normalized
}
