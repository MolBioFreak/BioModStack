#!/bin/bash

set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Rebuilding GPU Containers with RTX 5090 Support              ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "This will rebuild 4 containers with PyTorch 2.5.1 + CUDA 12.4"
echo "Estimated time: 30-45 minutes"
echo ""

cd apptainer
mkdir -p ../containers_backup

# Backup old containers
echo "[INFO] Backing up old containers..."
mv ../containers/rfdiffusion.sif ../containers_backup/ 2>/dev/null || true
mv ../containers/fampnn.sif ../containers_backup/ 2>/dev/null || true
mv ../containers/dl_binder_design.sif ../containers_backup/ 2>/dev/null || true
mv ../containers/boltz2.sif ../containers_backup/ 2>/dev/null || true

# Build containers in parallel (2 at a time to avoid overload)
echo "[INFO] Building rfdiffusion and fampnn in parallel..."
apptainer build --fakeroot ../containers/rfdiffusion.sif rfdiffusion.def > /tmp/rfdiffusion_rebuild.log 2>&1 &
PID1=$!
apptainer build --fakeroot ../containers/fampnn.sif fampnn.def > /tmp/fampnn_rebuild.log 2>&1 &
PID2=$!

wait $PID1
echo "[SUCCESS] RFdiffusion rebuilt"
wait $PID2  
echo "[SUCCESS] FAMPNN rebuilt"

echo "[INFO] Building dl_binder_design and boltz2 in parallel..."
apptainer build --fakeroot ../containers/dl_binder_design.sif dl_binder_design.def > /tmp/dlbinder_rebuild.log 2>&1 &
PID3=$!
apptainer build --fakeroot ../containers/boltz2.sif boltz2.def > /tmp/boltz2_rebuild.log 2>&1 &
PID4=$!

wait $PID3
echo "[SUCCESS] DL Binder Design rebuilt"
wait $PID4
echo "[SUCCESS] Boltz2 rebuilt"

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Rebuild Complete!                                             ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "All 4 GPU containers now support RTX 5090 (compute capability 12.0)"
echo ""
ls -lh ../containers/*.sif
echo ""
echo "Old containers backed up to: containers_backup/"
echo "Logs available at: /tmp/*_rebuild.log"
