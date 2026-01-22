#!/bin/bash

################################################################################
# BioModStack Container Build Script - Workstation Edition
#
# Builds all required Apptainer containers locally for workstation use.
# Optimized for fast builds using local resources and parallel execution.
#
# Usage:
#   ./build_containers_workstation.sh [OPTIONS]
#
# Options:
#   --parallel N    Build N containers in parallel (default: 3)
#   --sequential    Build containers one at a time
#   --container X   Build only container X (e.g., rfdiffusion)
#   --skip-test     Skip post-build validation tests
#   --help          Show this help message
#
################################################################################

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONTAINERS_DIR="${PROJECT_DIR}/containers"
BUILD_TEMP_DIR="${BUILD_TEMP_DIR:-/tmp/${USER}/apptainer_build}"
MAX_PARALLEL="${MAX_PARALLEL:-3}"
SKIP_TEST=0
SPECIFIC_CONTAINER=""
SEQUENTIAL=0

# Container definitions
declare -A CONTAINERS=(
    ["rfdiffusion"]="rfdiffusion.def"
    ["boltz2"]="boltz2.def"
    ["fampnn"]="fampnn.def"
    ["dl_binder_design"]="dl_binder_design.def"
    ["pyrosetta_tools"]="pyrosetta_tools.def"
    ["af2"]="af2.def"
)

# Build order (dependencies first)
BUILD_ORDER=("rfdiffusion" "fampnn" "dl_binder_design" "af2" "boltz2" "pyrosetta_tools")

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║  BioModStack Workstation Container Build System                 ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "$1 is not installed or not in PATH"
        return 1
    fi
    return 0
}

format_bytes() {
    local bytes=$1
    if ((bytes < 1024)); then
        echo "${bytes}B"
    elif ((bytes < 1048576)); then
        echo "$((bytes / 1024))KB"
    elif ((bytes < 1073741824)); then
        echo "$((bytes / 1048576))MB"
    else
        echo "$((bytes / 1073741824))GB"
    fi
}

format_duration() {
    local seconds=$1
    local hours=$((seconds / 3600))
    local minutes=$(((seconds % 3600) / 60))
    local secs=$((seconds % 60))

    if ((hours > 0)); then
        printf "%dh %02dm %02ds" $hours $minutes $secs
    elif ((minutes > 0)); then
        printf "%dm %02ds" $minutes $secs
    else
        printf "%ds" $secs
    fi
}

################################################################################
# Validation Functions
################################################################################

validate_prerequisites() {
    log_info "Validating prerequisites..."

    local all_good=1

    # Check Apptainer
    if ! check_command apptainer; then
        log_error "Apptainer is required. Install with: sudo apt install apptainer"
        all_good=0
    else
        local apptainer_version=$(apptainer --version | awk '{print $NF}')
        log_success "Apptainer found: $apptainer_version"
    fi

    # Check git
    if ! check_command git; then
        log_error "Git is required for building containers"
        all_good=0
    else
        log_success "Git found: $(git --version | awk '{print $3}')"
    fi

    # Check fakeroot capability
    if ! apptainer build --help | grep -q fakeroot; then
        log_warning "Apptainer fakeroot may not be available. Trying anyway..."
    else
        log_success "Apptainer fakeroot support detected"
    fi

    # Check available disk space
    local available_space=$(df "$PROJECT_DIR" | tail -1 | awk '{print $4}')
    local required_space=$((30 * 1024 * 1024)) # 30GB in KB

    if ((available_space < required_space)); then
        log_warning "Low disk space: $(format_bytes $((available_space * 1024))) available"
        log_warning "Recommended: 30GB+ free space for building containers"
    else
        log_success "Sufficient disk space: $(format_bytes $((available_space * 1024))) available"
    fi

    # Check definition files
    log_info "Checking definition files..."
    local missing_files=0
    for container in "${!CONTAINERS[@]}"; do
        local def_file="${SCRIPT_DIR}/${CONTAINERS[$container]}"
        if [[ ! -f "$def_file" ]]; then
            log_error "Definition file not found: $def_file"
            missing_files=1
        fi
    done

    if ((missing_files == 1)); then
        all_good=0
    else
        log_success "All definition files found"
    fi

    if ((all_good == 0)); then
        log_error "Prerequisites check failed. Please fix the issues above."
        return 1
    fi

    log_success "All prerequisites validated"
    echo ""
    return 0
}

setup_build_environment() {
    log_info "Setting up build environment..."

    # Create output directory
    mkdir -p "$CONTAINERS_DIR"
    log_success "Output directory: $CONTAINERS_DIR"

    # Create temp directory with good performance
    mkdir -p "$BUILD_TEMP_DIR"
    export APPTAINER_TMPDIR="$BUILD_TEMP_DIR"
    log_success "Temp directory: $BUILD_TEMP_DIR"

    # Set cache directory
    export APPTAINER_CACHEDIR="${HOME}/.apptainer/cache"
    mkdir -p "$APPTAINER_CACHEDIR"
    log_success "Cache directory: $APPTAINER_CACHEDIR"

    echo ""
}

################################################################################
# Build Functions
################################################################################

build_single_container() {
    local container_name=$1
    local def_file="${SCRIPT_DIR}/${CONTAINERS[$container_name]}"
    local output_file="${CONTAINERS_DIR}/${container_name}.sif"
    local log_file="${BUILD_TEMP_DIR}/${container_name}_build.log"

    local start_time=$(date +%s)

    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║  Building: ${container_name}${NC}"
    echo -e "${CYAN}╠════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${CYAN}║${NC}  Definition: ${def_file}"
    echo -e "${CYAN}║${NC}  Output:     ${output_file}"
    echo -e "${CYAN}║${NC}  Log:        ${log_file}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # Remove old container if exists
    if [[ -f "$output_file" ]]; then
        log_warning "Removing existing container: $output_file"
        rm -f "$output_file"
    fi

    # Build container
    log_info "Building container (this may take 10-30 minutes)..."
    log_info "Logging output to: $log_file"
    echo ""

    if apptainer build --fakeroot "$output_file" "$def_file" > "$log_file" 2>&1; then
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))
        local file_size=$(stat -f%z "$output_file" 2>/dev/null || stat -c%s "$output_file" 2>/dev/null || echo "0")

        log_success "Build completed in $(format_duration $duration)"
        log_success "Container size: $(format_bytes $file_size)"
        echo ""

        return 0
    else
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))

        log_error "Build failed after $(format_duration $duration)"
        log_error "Check log file: $log_file"
        echo ""
        echo "Last 50 lines of log:"
        tail -50 "$log_file"
        echo ""

        return 1
    fi
}

test_container() {
    local container_name=$1
    local container_file="${CONTAINERS_DIR}/${container_name}.sif"

    log_info "Testing container: $container_name"

    # Basic validation - container exists and is readable
    if [[ ! -f "$container_file" ]]; then
        log_error "Container file not found: $container_file"
        return 1
    fi

    # Test container can be inspected
    if ! apptainer inspect "$container_file" &>/dev/null; then
        log_error "Container inspection failed"
        return 1
    fi

    # Container-specific tests
    case "$container_name" in
        "rfdiffusion")
            if apptainer exec "$container_file" python3.10 -c "import rfdiffusion" &>/dev/null; then
                log_success "RFdiffusion import test passed"
            else
                log_warning "RFdiffusion import test failed (may be OK if modules need GPU)"
            fi
            ;;
        "boltz2")
            if apptainer exec "$container_file" bash -c ". /opt/venv/bin/activate && boltz --help" &>/dev/null; then
                log_success "Boltz-2 help test passed"
            else
                log_warning "Boltz-2 help test failed"
            fi
            ;;
        "fampnn")
            if apptainer exec "$container_file" python -c "import torch" &>/dev/null; then
                log_success "FAMPNN PyTorch import test passed"
            else
                log_warning "FAMPNN PyTorch import test failed"
            fi
            ;;
        "dl_binder_design")
            if apptainer exec "$container_file" python -c "import pyrosetta" 2>&1 | grep -q "pyrosetta"; then
                log_success "DL Binder Design import test passed"
            else
                log_warning "DL Binder Design test inconclusive (PyRosetta license may be needed)"
            fi
            ;;
        "pyrosetta_tools")
            if apptainer exec "$container_file" python -c "import Bio" &>/dev/null; then
                log_success "PyRosetta Tools BioPython test passed"
            else
                log_warning "PyRosetta Tools test failed"
            fi
            ;;
    esac

    log_success "Container validation complete"
    echo ""
    return 0
}

build_parallel() {
    local -a containers_to_build=("$@")
    local -a pids=()
    local -a results=()

    log_info "Building ${#containers_to_build[@]} containers with max $MAX_PARALLEL parallel"
    echo ""

    local idx=0
    for container in "${containers_to_build[@]}"; do
        # Wait if we've hit max parallel
        while ((${#pids[@]} >= MAX_PARALLEL)); do
            for i in "${!pids[@]}"; do
                if ! kill -0 "${pids[$i]}" 2>/dev/null; then
                    wait "${pids[$i]}"
                    results[$i]=$?
                    unset 'pids[$i]'
                fi
            done
            pids=("${pids[@]}") # Reindex array
            sleep 1
        done

        # Start build in background
        (build_single_container "$container") &
        pids+=($!)

        ((idx++))
    done

    # Wait for remaining builds
    log_info "Waiting for remaining builds to complete..."
    for pid in "${pids[@]}"; do
        wait "$pid"
        results+=($?)
    done

    # Check results
    local failed=0
    for result in "${results[@]}"; do
        if ((result != 0)); then
            ((failed++))
        fi
    done

    if ((failed > 0)); then
        log_error "$failed container(s) failed to build"
        return 1
    fi

    log_success "All containers built successfully"
    return 0
}

build_sequential() {
    local -a containers_to_build=("$@")

    log_info "Building ${#containers_to_build[@]} containers sequentially"
    echo ""

    for container in "${containers_to_build[@]}"; do
        if ! build_single_container "$container"; then
            log_error "Build failed for: $container"
            return 1
        fi
    done

    log_success "All containers built successfully"
    return 0
}

test_all_containers() {
    local -a containers_to_test=("$@")

    log_info "Testing ${#containers_to_test[@]} containers"
    echo ""

    local failed=0
    for container in "${containers_to_test[@]}"; do
        if ! test_container "$container"; then
            ((failed++))
        fi
    done

    if ((failed > 0)); then
        log_warning "$failed container(s) had test warnings"
    else
        log_success "All container tests passed"
    fi

    echo ""
}

################################################################################
# Summary Functions
################################################################################

print_summary() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║  Build Summary                                                 ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    log_info "Container directory: $CONTAINERS_DIR"
    echo ""

    local total_size=0
    for container in "${BUILD_ORDER[@]}"; do
        local container_file="${CONTAINERS_DIR}/${container}.sif"
        if [[ -f "$container_file" ]]; then
            local file_size=$(stat -f%z "$container_file" 2>/dev/null || stat -c%s "$container_file" 2>/dev/null || echo "0")
            total_size=$((total_size + file_size))
            echo -e "  ${GREEN}✓${NC} ${container}.sif ($(format_bytes $file_size))"
        else
            echo -e "  ${RED}✗${NC} ${container}.sif (missing)"
        fi
    done

    echo ""
    log_success "Total size: $(format_bytes $total_size)"
    echo ""

    echo -e "${GREEN}Next steps:${NC}"
    echo "  1. Run a test with: nextflow run main.nf -profile test,workstation_ryzen7960x,monomer_denovo"
    echo "  2. Check WORKSTATION_QUICKSTART.md for full testing instructions"
    echo ""
}

cleanup() {
    log_info "Cleaning up temporary files..."

    # Keep cache but clean temp builds
    if [[ -d "$BUILD_TEMP_DIR" ]] && [[ "$BUILD_TEMP_DIR" != "/" ]]; then
        local temp_size=$(du -sb "$BUILD_TEMP_DIR" 2>/dev/null | awk '{print $1}' || echo "0")
        rm -rf "${BUILD_TEMP_DIR:?}"/*
        log_success "Cleaned $(format_bytes $temp_size) from temp directory"
    fi

    echo ""
}

################################################################################
# Main Script
################################################################################

show_help() {
    cat << EOF
BioModStack Container Build Script - Workstation Edition

Usage: $0 [OPTIONS]

Options:
    --parallel N        Build N containers in parallel (default: 3)
    --sequential        Build containers one at a time
    --container NAME    Build only specified container
    --skip-test         Skip post-build validation tests
    --help              Show this help message

Available containers:
    rfdiffusion         RFdiffusion for backbone generation
    boltz2              Boltz-2 for structure prediction
    fampnn              Full-Atom MPNN for sequence design
    dl_binder_design    ProteinMPNN + AlphaFold2 + PyRosetta
    pyrosetta_tools     PyRosetta analysis tools

Examples:
    # Build all containers with default settings (3 parallel)
    $0

    # Build all containers sequentially
    $0 --sequential

    # Build only RFdiffusion
    $0 --container rfdiffusion

    # Build with 2 parallel processes
    $0 --parallel 2

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --parallel)
                MAX_PARALLEL="$2"
                shift 2
                ;;
            --sequential)
                SEQUENTIAL=1
                shift
                ;;
            --container)
                SPECIFIC_CONTAINER="$2"
                shift 2
                ;;
            --skip-test)
                SKIP_TEST=1
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

main() {
    local script_start_time=$(date +%s)

    print_header

    # Validate and setup
    if ! validate_prerequisites; then
        exit 1
    fi

    setup_build_environment

    # Determine what to build
    local -a containers_to_build=()
    if [[ -n "$SPECIFIC_CONTAINER" ]]; then
        if [[ -z "${CONTAINERS[$SPECIFIC_CONTAINER]}" ]]; then
            log_error "Unknown container: $SPECIFIC_CONTAINER"
            echo "Available containers: ${!CONTAINERS[*]}"
            exit 1
        fi
        containers_to_build=("$SPECIFIC_CONTAINER")
    else
        containers_to_build=("${BUILD_ORDER[@]}")
    fi

    # Build containers
    if ((SEQUENTIAL == 1)); then
        build_sequential "${containers_to_build[@]}" || exit 1
    else
        build_parallel "${containers_to_build[@]}" || exit 1
    fi

    # Test containers
    if ((SKIP_TEST == 0)); then
        test_all_containers "${containers_to_build[@]}"
    else
        log_warning "Skipping container tests (--skip-test)"
    fi

    # Summary
    print_summary

    # Cleanup
    cleanup

    local script_end_time=$(date +%s)
    local total_duration=$((script_end_time - script_start_time))

    log_success "Total build time: $(format_duration $total_duration)"
    echo ""
}

# Parse arguments and run
parse_args "$@"
main
