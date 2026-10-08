// Offline validator output fixtures; never scientific predictions.
process BatchBoltzValidation {
 input:
 path pdbs
 path msa
 output:
 path '*_boltzpred.pdb', emit: raw_pdbs
 path '*_boltzpred.json', emit: raw_scores
 path '*_boltzpred.pae.npz', emit: raw_aligned_error
 path 'originals/*.pdb', emit: original_designs
 script:
 def files = pdbs instanceof Collection ? pdbs : [pdbs]
 def commands = files.collect { pdb -> "cp ${pdb} ${pdb.baseName}_boltzpred.pdb; printf '{}' > ${pdb.baseName}_boltzpred.json; printf fixture > ${pdb.baseName}_boltzpred.pae.npz" }.join('\n')
 """
 mkdir originals
 cp ${pdbs} originals/
 ${commands}
 """
}
process AlignBoltzValidation {
 input:
 path pdbs
 path scores
 path errors
 path originals
 output:
 path 'predictions/*.pdb', emit: pdbs
 path 'predictions/*.json', emit: scores
 path 'predictions/*.npz', emit: aligned_error
 script:
 """
 mkdir predictions
 cp ${pdbs} ${scores} ${errors} predictions/
 """
}
process BatchProtenixValidation {
 input:
 path pdbs
 path msa
 output:
 path 'predictions/*.pdb', emit: pdbs
 path 'predictions/*.json', emit: scores
 path 'predictions/*.cif', emit: cifs
 path 'predictions/*.npz', emit: aligned_error
 script:
 def files = pdbs instanceof Collection ? pdbs : [pdbs]
 def commands = files.collect { pdb -> "cp ${pdb} predictions/${pdb.baseName}_sample_0.pdb; printf '{}' > predictions/${pdb.baseName}_sample_0.json; printf fixture > predictions/${pdb.baseName}_sample_0.cif; printf fixture > predictions/${pdb.baseName}_sample_0.npz" }.join('\n')
 """
 mkdir predictions
 ${commands}
 """
}
process BatchESMFold2Validation {
 input:
 path pdbs
 path msa
 output:
 path 'predictions/*.pdb', emit: pdbs
 path 'predictions/*.cif', emit: cifs
 path 'predictions/*.json', emit: metrics
 script:
 def files = pdbs instanceof Collection ? pdbs : [pdbs]
 def commands = files.collect { pdb -> "cp ${pdb} predictions/${pdb.baseName}_esmfold2.pdb; printf '{}' > predictions/${pdb.baseName}_esmfold2.json; printf fixture > predictions/${pdb.baseName}_esmfold2.cif" }.join('\n')
 """
 mkdir predictions
 ${commands}
 """
}
process BatchImmunogenicity {
 input:
 path pdbs
 output:
 path 'scores.csv', emit: scores
 script:
 "printf fixture > scores.csv"
}
process BatchStability {
 input:
 path pdbs
 output:
 path 'scores.csv', emit: scores
 script:
 "printf fixture > scores.csv"
}
