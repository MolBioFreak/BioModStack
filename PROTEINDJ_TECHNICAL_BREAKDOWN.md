# ProteinDJ Technical Breakdown: Container Orchestration & Execution

## Container Build System

### Apptainer Definition Files

ProteinDJ uses Apptainer (formerly Singularity) definition files (`.def`) to build containers.

**Location:** `apptainer/*.def`

**Build process:**
```bash
# Manual build
apptainer build rfdiffusion.sif apptainer/rfdiffusion.def

# Automated build script
bash apptainer/build_containers.sh
```

### Definition File Structure

Every `.def` file has these sections:

```apptainer
Bootstrap: docker          # Base image source (docker, library, etc.)
From: nvidia/cuda:11.8.0   # Base image

%post
    # Installation commands (like Dockerfile RUN)
    apt-get update
    apt-get install python3
    pip install package

%environment
    # Environment variables (like Dockerfile ENV)
    export VAR=value

%runscript
    # Default command when container runs (like Dockerfile ENTRYPOINT)
    exec python /app/script.py "$@"

%test
    # Tests run after build (optional)
    python --version

%help
    # Help text (optional)
    This container runs XYZ
```

### Example: RFdiffusion Container

**File:** `apptainer/rfdiffusion.def`

```apptainer
Bootstrap: docker
From: nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

%post
    # Install system deps
    apt-get update
    apt-get install git python3.10 python3-pip

    # Clone specific commit of RFdiffusion
    git clone https://github.com/PapenfussLab/RFdiffusion /app/RFdiffusion
    cd /app/RFdiffusion
    git checkout 0938f878388466d30d16f1f468c362afe9c8d345

    # Install Python deps
    pip install dgl==1.0.2+cu116
    pip install torch==2.0.1
    pip install /app/RFdiffusion

%environment
    DGLBACKEND="pytorch"

%runscript
    exec python3.10 /app/RFdiffusion/scripts/run_inference.py "$@"
```

**Key points:**
- Uses NVIDIA CUDA base image for GPU support
- Pins specific RFdiffusion commit for reproducibility
- Installs exact package versions
- Default runscript is RFdiffusion inference script
- Sets required environment variables

### Example: Boltz-2 Container

**File:** `apptainer/boltz2.def`

```apptainer
Bootstrap: docker
Stage: build
From: nvidia/cuda:12.8.0-base-ubuntu24.04

%post
    # Install build deps
    apt-get update
    apt-get install git wget python3-pip build-essential

    # Create virtual environment
    python3 -m venv /opt/venv
    . /opt/venv/bin/activate

    # Install Boltz from specific commit
    git clone https://github.com/jwohlwend/boltz.git
    cd /boltz
    git checkout aaef502
    pip install .

    # Activate venv on container start
    echo '. /opt/venv/bin/activate' >> $APPTAINER_ENVIRONMENT

%runscript
    cd /boltz
    exec "$@"

%test
    . /opt/venv/bin/activate
    boltz --help
```

**Key points:**
- Multi-stage build pattern (build stage only)
- Uses Python venv for isolation
- Activates venv automatically via $APPTAINER_ENVIRONMENT
- Flexible runscript (executes whatever command passed)
- Includes test to validate build

## Container Storage & Distribution

### Cloud Storage

ProteinDJ hosts pre-built containers on OpenStack object storage:

```
URL: https://object-store.rc.nectar.org.au/v1/AUTH_b6f9bdf15faf4320a7587fd42f62e530/ContainerHub/

Files:
├── rfdiffusion.sif        (~2.5 GB)
├── dl_binder_design.sif   (~5 GB, contains ProteinMPNN + PyRosetta + AF2)
├── fampnn.sif             (~1.5 GB)
├── boltz2.sif             (~3 GB)
└── pyrosetta_tools.sif    (~4 GB)
```

### Configuration

**In nextflow.config:**

```groovy
params {
    // Default: Pull from cloud
    container_dir = 'https://object-store.rc.nectar.org.au/v1/AUTH_.../ContainerHub'

    // For local builds or custom location
    // container_dir = '/path/to/local/containers'
}

profiles {
    apptainer {
        apptainer.enabled = true
        docker.enabled = false
        params.container_dir = "https://..."  // Cloud URL
    }

    milton {  // WEHI HPC
        apptainer.enabled = true
        params.container_dir = "/vast/projects/.../containers/proteinDJ/18_08"
    }
}
```

### Automatic Download

When Nextflow runs with `apptainer.enabled = true`:

1. Checks if container exists in cache (`~/.apptainer/cache/`)
2. If not found, downloads from `container_dir` URL
3. Stores in cache for reuse
4. Mounts container as read-only overlay filesystem

**Cache location:** `$HOME/.apptainer/cache/` or `$APPTAINER_CACHEDIR`

## Process Definition Anatomy

### Process Structure

Every Nextflow process has this structure:

```groovy
process ProcessName {
    // Labels for resource requirements
    label 'container_label'
    label 'resource_label'

    // Output publishing
    publishDir "${params.out_dir}/subdir", mode: 'copy'

    // Unique identifier for this task instance
    tag "identifier"

    // Input channels
    input:
    path input_file
    val parameter

    // Output channels
    output:
    path "output/*", emit: files
    path "*.log", emit: logs

    // Shell script to execute
    script:
    """
    command --input ${input_file} --param ${parameter}
    """
}
```

### Example: RunRFDiffusion Process

**File:** `modules/rfdiffusion.nf`

```groovy
process RunRFDiffusion {
    label 'RFDiffusion'    // → Uses rfdiffusion.sif container
    label 'gpu'            // → Requests GPU resources
    tag "B${batch_id}"     // → Shows "B0", "B1" in log

    publishDir "${params.out_dir}/run/rfd", mode: 'copy', pattern: "*.log"

    beforeScript """
        mkdir -p outputs schedules .dgl
    """

    input:
    tuple val(rfd_command),      // Command string
          val(batch_id),         // Batch number
          val(batch_size),       // Designs in this batch
          val(design_startnum),  // Starting design number
          val(mode),             // Design mode
          path(input_files)      // Input PDB files

    output:
    path("rfd_results/*.pdb"), emit: pdbs
    tuple path("rfd_results/*.pdb"),
          path("rfd_results/*.json"), emit: pdbs_jsons
    path("*.log")
    path("rfd_metadata_${batch_id}.jsonl"), topic: metadata_ch_fold

    script:
    """
    echo "Running RFdiffusion for batch ${batch_id} in ${mode} mode"

    python3.10 ${rfd_command} \
        inference.model_directory_path=/app/RFdiffusion/models \
        inference.schedule_directory_path=/app/RFdiffusion/schedules \
        inference.design_startnum=${design_startnum} \
        inference.num_designs=${batch_size} \
        2>&1 | tee rfd_${batch_id}.log

    python3.10 /scripts/metadata_converter.py \
        --input_dir rfd_results \
        --converter rfd \
        --input_ext trb \
        -o rfd_metadata_${batch_id}.jsonl
    """
}
```

**Breakdown:**

**Labels:**
- `label 'RFDiffusion'` → Selects container and bindings (see Process Configuration)
- `label 'gpu'` → Requests GPU allocation

**beforeScript:**
- Runs before main script
- Creates required directories (RFdiffusion expects these)

**Input tuple:**
- Receives 6 values from upstream channel
- `path(input_files)` automatically stages files in work directory

**Output:**
- `emit: pdbs` → Named output channel for PDB files
- `topic: metadata_ch_fold` → Publishes to topic channel for metadata aggregation

**Script:**
- Executes inside container
- Paths like `/app/RFdiffusion/models` are inside container
- `${rfd_command}` is interpolated from input
- `2>&1 | tee` captures both stdout and stderr to log file

### Example: RunBoltz Process

**File:** `modules/boltz.nf`

```groovy
process RunBoltz {
    label 'Boltz'
    label 'gpu'
    tag "B${batch_id}"

    publishDir "${params.out_dir}/run/boltz", mode: 'copy', pattern: "*.log"

    input:
    tuple val(batch_id), path(yamls)

    output:
    tuple path("predictions/*.pdb"),
          path("predictions/*.json"), emit: pdbs_jsons
    path("*.log"), emit: logs

    script:
    """
    # Workaround: Some packages write to HOME
    mkdir tmp
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp

    # Copy symlinked files to real files
    mkdir yamls
    for file in \$(find *.yaml); do
        cp -L "\$file" ./yamls/
    done

    # Run Boltz prediction
    boltz predict \
        ./yamls/ \
        --output_format pdb \
        --diffusion_samples ${params.boltz_diffusion_samples} \
        --recycling_steps ${params.boltz_recycling_steps} \
        --sampling_steps ${params.boltz_sampling_steps} \
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \
        --cache /boltzcache \
        ${params.boltz_extra_config ?: ''} \
        2>&1 | tee boltz_${batch_id}.log

    # Reorganize output files
    mkdir -p predictions
    for dir in boltz_results_yamls/predictions/fold_*_seq_*; do
        inputname=\$(basename "\$dir")

        if [ -f "\${dir}/\${inputname}_model_0.pdb" ]; then
            mv "\${dir}/\${inputname}_model_0.pdb" \
               "predictions/\${inputname}_boltzpred.pdb"
        fi

        if [ -f "\${dir}/confidence_\${inputname}_model_0.json" ]; then
            mv "\${dir}/confidence_\${inputname}_model_0.json" \
               "predictions/\${inputname}_boltzpred.json"
        fi
    done
    """
}
```

**Key techniques:**

**Temporary HOME:**
- Some Python packages (numba, triton) write cache to HOME
- Container HOME might be read-only
- Create `tmp/` and override HOME to allow writes

**Dereference symlinks:**
- Nextflow stages files as symlinks
- `cp -L` copies actual file content
- Some tools don't follow symlinks correctly

**Conditional parameters:**
- `${params.boltz_use_potentials ? '--use_potentials' : ''}`
- Groovy ternary operator
- Adds flag only if parameter is true

**File reorganization:**
- Boltz outputs to nested directories with complex names
- Script flattens structure and renames to expected format
- `fold_X_seq_X_boltzpred.pdb` format used by downstream

## Process Configuration

### Label-Based Configuration

**In nextflow.config:**

```groovy
process {
    // Defaults for all processes
    cpus = "${params.cpus}"              // 24
    memory = "${params.memory_cpu}"      // 24GB

    // GPU processes
    withLabel: 'gpu' {
        clusterOptions = '--gres=gpu:${params.gpu_model}:1'
        cpus = "${params.cpus_per_gpu}"  // 8
        memory = "${params.memory_gpu}"  // 24GB

        if (params.gpu_queue) {
            queue = "${params.gpu_queue}"
        }
    }

    // Container-specific configurations
    withLabel: 'RFDiffusion' {
        container = "${params.container_dir}/rfdiffusion.sif"
        containerOptions = """--nv \
            --bind ${params.rfd_models}:/app/RFdiffusion/models \
            --bind ./schedules:/app/RFdiffusion/schedules \
            --bind ./.dgl:\$HOME/.dgl \
            --bind ${projectDir}/scripts:/scripts \
            --bind ${projectDir}"""
    }

    withLabel: 'Boltz' {
        container = "${params.container_dir}/boltz2.sif"
        containerOptions = """--nv \
            --bind ${params.boltz_models}:/boltzcache \
            --bind ${projectDir}/scripts:/scripts \
            --bind ${projectDir}"""
    }

    withLabel: 'MPNN' {
        container = "${params.container_dir}/dl_binder_design.sif"
        containerOptions = """--bind ${projectDir}/scripts:/scripts \
            --bind ${projectDir}"""
    }

    withLabel: 'pyrosetta_tools' {
        container = "${params.container_dir}/pyrosetta_tools.sif"
        containerOptions = "--bind ${projectDir} \
            --bind ${projectDir}/scripts:/scripts"
    }
}
```

**How it works:**

1. Process declares labels: `label 'RFDiffusion'` and `label 'gpu'`
2. Nextflow merges all matching `withLabel` configurations
3. Final configuration for RunRFDiffusion:
   - `cpus = 8` (from gpu label)
   - `memory = 24GB` (from gpu label)
   - `clusterOptions = --gres=gpu:A30:1` (from gpu label)
   - `container = .../rfdiffusion.sif` (from RFDiffusion label)
   - `containerOptions = --nv --bind ...` (from RFDiffusion label)

### Container Options Explained

**--nv:**
- Enables NVIDIA GPU support
- Mounts GPU devices and CUDA libraries into container
- Required for GPU processes

**--bind syntax:**
```bash
--bind /host/path:/container/path
```
- Mounts host directory into container
- Can be read-write or read-only
- Multiple binds separated by spaces or multiple --bind flags

**Common binds:**

```bash
# Model weights (read-only)
--bind ${params.rfd_models}:/app/RFdiffusion/models

# Scripts directory (read-only)
--bind ${projectDir}/scripts:/scripts

# Working directory (read-write)
--bind ${projectDir}
```

**Variable expansion:**
- `${params.rfd_models}` → Groovy variable from nextflow.config
- `${projectDir}` → Nextflow builtin, path to pipeline directory
- `\$HOME` → Shell variable (escaped for shell, not Groovy)

### GPU Resource Allocation

**For SLURM:**

```groovy
withLabel: 'gpu' {
    clusterOptions = '--gres=gpu:${params.gpu_model}:1'
    queue = "${params.gpu_queue}"
}
```

Translates to SLURM command:
```bash
sbatch --gres=gpu:A30:1 --partition=gpuq script.sh
```

**For local execution (not in ProteinDJ yet, but for Docker):**

```groovy
withLabel: 'gpu' {
    containerOptions = "--gpus device=${task.index % params.gpus}"
}
```

Assigns GPUs round-robin:
- Task 0 → GPU 0
- Task 1 → GPU 1
- Task 2 → GPU 2
- Task 3 → GPU 3
- Task 4 → GPU 0 (wraps around)

## Workflow Orchestration

### Main Workflow

**File:** `main.nf`

**Execution flow:**

```groovy
workflow {
    // 1. Parameter validation
    if (params.run_rfd_only && params.skip_rfd) {
        error("Contradictory flags")
    }

    // 2. Calculate batching
    def num_batches = Math.min(params.gpus, params.rfd_num_designs)
    def batch_size = Math.ceil(params.rfd_num_designs / num_batches)

    // 3. Create output directories
    def configDir = file("${params.out_dir}/configs")
    configDir.mkdirs()

    // 4. Run RFdiffusion (Stage 1)
    if (!params.skip_rfd) {
        RFDiffusionWorkflow(...)
        RFDiffusionWorkflow.out.pdbs_jsons.set { rfd_pdbs_jsons }
        CompressRFD("rfd", rfd_pdbs_jsons.flatten().collect())
        FilterRFD(rfd_pdbs_jsons)
        FilterRFD.out.pdbs_jsons.set { filt_rfd_pdbs_jsons }
    }

    // 5. Run Sequence Design (Stage 2)
    if (!params.skip_rfd_seq && !params.run_rfd_only) {
        if (params.seq_method == "mpnn") {
            PrepMPNN(filt_rfd_pdbs_jsons)
            RunMPNN(PrepMPNN.out.pdbs)
            FilterMPNN(RunMPNN.out.pdbs_jsons)
            FilterMPNN.out.pdbs.set { filt_seq_pdbs }
        } else if (params.seq_method == "fampnn") {
            PrepFAMPNN(filt_rfd_pdbs_jsons)
            RunFAMPNN(PrepFAMPNN.out.pdbs)
            FilterFAMPNN(RunFAMPNN.out.pdbs_jsons)
            FilterFAMPNN.out.pdbs.set { filt_seq_pdbs }
        }
    }

    // 6. Run Structure Prediction (Stage 3)
    if (!params.skip_rfd_seq_pred && !params.run_rfd_only) {
        if (params.pred_method == "af2") {
            RunAF2(filt_seq_pdbs)
            FilterAF2(RunAF2.out.pdbs_jsons)
            FilterAF2.out.pdbs.set { analysis_input_pdbs }
        } else if (params.pred_method == "boltz") {
            PrepBoltz(filt_seq_pdbs)
            RunBoltz(PrepBoltz.out.yamls)
            AlignBoltz(RunBoltz.out.pdbs_jsons, filt_seq_pdbs)
            FilterBoltz(AlignBoltz.out.pdbs_jsons)
            FilterBoltz.out.pdbs.set { analysis_input_pdbs }
        }
    }

    // 7. Run Analysis (Stage 6)
    if (!params.run_rfd_only) {
        AnalyseBestDesigns(analysis_input_pdbs)
        analysis_input_pdbs.set { final_pdbs }
    }

    // 8. Collect metadata from topic channels
    channel.topic('metadata_ch_fold')
        .collectFile(name: "metadata_fold.jsonl")
        .set { metadata_fold }

    channel.topic('metadata_ch_fold_seq')
        .collectFile(name: "metadata_fold_seq.jsonl")
        .set { metadata_fold_seq }

    // 9. Combine metadata
    CombineMetadata(metadata_fold, metadata_fold_seq)
        .csv
        .collectFile(name: "all_designs.csv")
        .set { all_designs_metadata }

    // 10. Publish results
    PublishResults(final_pdbs, all_designs_metadata, ...)
}
```

**Key patterns:**

**Channel assignment:**
```groovy
ProcessName.out.pdbs.set { variable_name }
```
Creates channel `variable_name` from process output.

**Conditional execution:**
```groovy
if (!params.skip_rfd) {
    RunRFDiffusion(...)
}
```
Entire code block skipped if condition false.

**Method selection:**
```groovy
if (params.seq_method == "mpnn") {
    RunMPNN(...)
} else if (params.seq_method == "fampnn") {
    RunFAMPNN(...)
}
```
Different process paths based on parameters.

### Sub-Workflow

**File:** `workflows/rfdiffusion.nf`

```groovy
workflow RFDiffusionWorkflow {
    take:
    rfdCommand       // Input parameter
    numDesigns
    batchSize
    mode
    inputFiles

    main:
    // Create channel for batches
    rf_ch = Channel
        .fromList((0..<numDesigns).collate(batchSize))
        .map { batch ->
            def batchId = (batch[0] / batchSize).intValue()
            def designStartnum = batch.min()
            [rfdCommand, batchId, batchSize, designStartnum, mode, inputFiles]
        }

    // Run RFdiffusion on each batch
    RunRFDiffusion(rf_ch)

    emit:
    pdbs_jsons = RunRFDiffusion.out.pdbs_jsons
}
```

**Channel creation breakdown:**

```groovy
// If numDesigns=8, batchSize=2:
(0..<8)                    // [0,1,2,3,4,5,6,7]
.collate(2)                // [[0,1], [2,3], [4,5], [6,7]]
.map { batch ->
    def batchId = (batch[0] / 2).intValue()
    def designStartnum = batch.min()
    [rfdCommand, batchId, batchSize, designStartnum, mode, inputFiles]
}
// Result: 4 tuples for 4 batches
// [[cmd, 0, 2, 0, mode, files],
//  [cmd, 1, 2, 2, mode, files],
//  [cmd, 2, 2, 4, mode, files],
//  [cmd, 3, 2, 6, mode, files]]
```

Each tuple becomes one RunRFDiffusion task.

## Channel Operations

### Batching Operations

**Utils.rebatchTuples()** - Rebatch tuple channels:

```groovy
// lib/Utils.groovy
static def rebatchTuples(input_channel, batch_size = 50) {
    return input_channel
        .transpose()           // [[pdbs], [jsons]] → [pdb1, json1], [pdb2, json2], ...
        .buffer(size: batch_size)  // Group into batches of 50
        .map { pairs ->        // Reconstruct tuple format
            def first = pairs.collect { it[0] }   // All PDBs
            def second = pairs.collect { it[1] }  // All JSONs
            return [first, second]
        }
}
```

**Utils.rebatchGPU()** - Batch for GPU distribution:

```groovy
static def rebatchGPU(input_channel, gpus) {
    return input_channel
        .collect()                // Wait for all items
        .flatMap { all_pdbs ->    // Process as list
            def total_size = all_pdbs.size()
            def nbatches = Math.min(gpus, total_size)
            def bsize = (total_size / nbatches).doubleValue()

            def idx = 0
            all_pdbs.collect { pdb ->
                def batch_id = Math.floor(idx++ / bsize).intValue()
                [batch_id, pdb]   // Tag each PDB with batch ID
            }
        }
        .groupTuple()  // Group by batch_id
}
```

Example with 10 files, 4 GPUs:
```
Input: [file1, file2, ..., file10]
After flatMap: [[0, file1], [0, file2], [0, file3], [1, file4], [1, file5], ...]
After groupTuple: [[0, [file1, file2, file3]], [1, [file4, file5, file6]], ...]
```

**Utils.rebatchGPUByNumRes()** - Size-aware batching:

```groovy
static def rebatchGPUByNumRes(input_channel, gpus) {
    return input_channel
        .collect()
        .flatMap { all_pdbs ->
            // Count residues in each PDB
            def pdb_sizes = all_pdbs.collect { pdb ->
                def num_res = countResidues(pdb)
                [pdb, num_res]
            }

            // Sort by size
            pdb_sizes.sort { it[1] }

            // Distribute to GPUs (largest files to different GPUs)
            def batches = (0..<gpus).collect { [] }
            pdb_sizes.each { pdb, size ->
                // Find GPU with least total residues
                def min_gpu = batches.min { it.sum { it[1] } ?: 0 }
                min_gpu << pdb
            }

            // Return as batch ID tuples
            batches.collectWithIndex { batch, idx ->
                [idx, batch]
            }
        }
        .groupTuple()
}
```

Ensures large proteins don't all hit one GPU.

### Topic Channels

**Publishing to topic:**

```groovy
process RunRFDiffusion {
    output:
    path("metadata.jsonl"), topic: metadata_ch_fold

    script:
    """
    # Generate metadata
    python /scripts/metadata_converter.py ... > metadata.jsonl
    """
}
```

**Consuming topic:**

```groovy
workflow {
    // Topic channels accumulate all published items
    channel.topic('metadata_ch_fold')
        .flatten()
        .collectFile(name: "metadata_fold.jsonl", newLine: true)
        .set { metadata_fold }
}
```

**How it works:**
- Multiple processes can publish to same topic
- Topic channel accumulates all items
- Main workflow consumes topic once all processes complete
- Useful for gathering metadata from parallel tasks

### Channel Modifiers

**flatten()** - Convert lists to individual items:
```groovy
Channel.of([[1,2], [3,4]]).flatten()  // → 1, 2, 3, 4
```

**collect()** - Wait for all items, emit as single list:
```groovy
Channel.of(1, 2, 3).collect()  // → [1, 2, 3]
```

**buffer()** - Group into batches:
```groovy
Channel.of(1,2,3,4,5).buffer(size: 2)  // → [1,2], [3,4], [5]
```

**groupTuple()** - Group by first element:
```groovy
Channel.of([0,'a'], [1,'b'], [0,'c'])
    .groupTuple()  // → [0, ['a','c']], [1, ['b']]
```

**collectFile()** - Concatenate to file:
```groovy
Channel.of("line1", "line2")
    .collectFile(name: "output.txt", newLine: true)
```

**ifEmpty()** - Provide default if channel empty:
```groovy
channel.ifEmpty(file("placeholder.pdb"))
```

## Model Weights & Data

### Download Script

**File:** `scripts/download_models.sh`

Downloads model weights from public sources:

```bash
#!/bin/bash

# Create directories
mkdir -p models/rfd models/af2 models/boltz

# RFdiffusion models (~1.5 GB)
wget https://files.ipd.uw.edu/pub/RFdiffusion/models/Complex_base_ckpt.pt \
    -O models/rfd/Complex_base_ckpt.pt

# AlphaFold2 parameters (~3.5 GB)
wget https://storage.googleapis.com/alphafold/alphafold_params_2022-12-06.tar \
    -O af2_params.tar
tar -xf af2_params.tar -C models/af2/

# Boltz weights (~10 GB)
# (Downloaded automatically by Boltz on first run to cache directory)
```

### Model Paths

**In nextflow.config:**

```groovy
params {
    rfd_models = "${projectDir}/models/rfd"
    af2_models = "${projectDir}/models/af2"
    boltz_models = "${projectDir}/models/boltz"
}
```

**Container binding:**

```groovy
withLabel: 'RFDiffusion' {
    containerOptions = """--bind ${params.rfd_models}:/app/RFdiffusion/models"""
}
```

Maps `models/rfd/` on host to `/app/RFdiffusion/models` in container.

**Inside container:**

```python
# Python code in container
model_path = "/app/RFdiffusion/models/Complex_base_ckpt.pt"
model = torch.load(model_path)
```

Reads from host directory via bind mount.

### Cache Directories

Some tools cache data:

**Boltz:**
- Downloads models to cache on first run
- Host cache: `models/boltz/`
- Container cache: `/boltzcache`
- Binding: `--bind ${params.boltz_models}:/boltzcache`

**DGL (used by RFdiffusion):**
- Caches compiled kernels in `~/.dgl/`
- Container binding: `--bind ./.dgl:$HOME/.dgl`
- Ensures cache persists across runs

## Execution Modes

### Local Execution

```bash
nextflow run main.nf
```

**Executor:** `local` (default)
**Behavior:**
- Runs tasks as local processes
- Respects `maxForks` for parallelism
- Uses CPUs/memory defined in process config

### SLURM Cluster

```groovy
// nextflow.config
executor {
    name = 'slurm'
    queueSize = 100
}
```

```bash
nextflow run main.nf
```

**Behavior:**
- Submits tasks via `sbatch`
- Uses `clusterOptions` for SLURM parameters
- Queue limits concurrent jobs

### Resume After Failure

```bash
nextflow run main.nf -resume
```

**How it works:**
1. Nextflow hashes inputs for each task
2. Checks `work/` directory for matching hash
3. If found and successful, reuses cached output
4. Only reruns tasks with changed inputs or failed status

**Work directory:**
```
work/
├── 1a/2b3c4d...  # Task execution directory
│   ├── .command.sh     # Generated script
│   ├── .command.log    # Task output
│   ├── .exitcode       # Exit status
│   ├── input_file      # Symlink to input
│   └── output.pdb      # Task output
└── 5e/6f7g8h...  # Another task
```

Each task gets unique work directory. Outputs symlinked to final destination.

## Error Handling

### Filter-Based Rejection

Designs can fail filters at each stage:

**RFD Filtering:**
```python
# scripts/filter_rfd.py
if num_helices < min_helices or num_helices > max_helices:
    reject_design()
```

**Sequence Filtering:**
```python
# scripts/filter_mpnn.py
if mpnn_score > max_score:
    reject_design()
```

**Prediction Filtering:**
```python
# scripts/filter_boltz.py
if plddt < min_plddt or rmsd > max_rmsd:
    reject_design()
```

**In Nextflow:**
```groovy
output:
path("output/*.pdb"), emit: pdbs, optional: true
```

`optional: true` allows empty output if all designs filtered.

### Placeholder System

If stage produces no outputs:

```groovy
FilterBoltz.out.pdbs
    .flatten()
    .collect()
    .ifEmpty(file("${projectDir}/lib/placeholder.pdb"))
    .set { final_pdbs }
```

**lib/placeholder.pdb:**
```
REMARK No designs passed filtering
```

Ensures downstream processes don't fail on empty input.

### Process Retry

Can configure automatic retry:

```groovy
process {
    errorStrategy = 'retry'
    maxRetries = 3
}
```

Nextflow will retry failed tasks up to 3 times.

## Metadata System

### Metadata Flow

Each stage generates metadata:

```
RFdiffusion:
  - fold_id (e.g., fold_0)
  - pLDDT
  - secondary structure
  → metadata_ch_fold

Sequence Design:
  - fold_id + seq_id (e.g., fold_0_seq_0)
  - MPNN score
  - sequence
  → metadata_ch_fold_seq

Prediction:
  - fold_id + seq_id
  - pLDDT, PAE, RMSD
  - confidence metrics
  → metadata_ch_fold_seq
```

### JSONL Format

Intermediate metadata in JSONL (JSON Lines):

```json
{"fold_id": "fold_0", "plddt": 85.2, "num_helices": 3}
{"fold_id": "fold_1", "plddt": 78.9, "num_helices": 2}
```

One JSON object per line.

### Metadata Converter

**File:** `scripts/metadata_converter.py`

Extracts metadata from various formats:

```python
def convert_rfd_metadata(trb_file):
    # Read RFdiffusion .trb file (pickle)
    with open(trb_file, 'rb') as f:
        data = pickle.load(f)

    return {
        'fold_id': data['design_id'],
        'plddt': data['plddt'].mean(),
        'inpaint_str': data.get('inpaint_str'),
        # ...
    }

def convert_boltz_metadata(json_file):
    # Read Boltz confidence JSON
    with open(json_file) as f:
        data = json.load(f)

    return {
        'fold_id': extract_id(json_file),
        'seq_id': extract_seq_id(json_file),
        'ptm': data['ptm'],
        'plddt': data['plddt'],
        # ...
    }
```

### CSV Aggregation

**File:** `modules/combine_metadata.nf`

```groovy
process CombineMetadata {
    input:
    path fold_metadata
    path fold_seq_metadata

    output:
    path "all_designs.csv", emit: csv

    script:
    """
    python /scripts/combine_metadata.py \
        --fold ${fold_metadata} \
        --fold_seq ${fold_seq_metadata} \
        --output all_designs.csv
    """
}
```

Merges JSONL files into single CSV with all metrics.

**Output format:**
```csv
fold_id,seq_id,plddt,mpnn_score,rmsd,ptm,...
fold_0,seq_0,85.2,2.3,0.8,0.92,...
fold_0,seq_1,83.1,2.5,1.1,0.89,...
```

One row per design, all metrics in columns.
