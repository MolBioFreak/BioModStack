#!/bin/bash
# Download all RFdiffusion model weights
# Total size: ~8-10 GB

set -e

MODELS_DIR="models"
mkdir -p "$MODELS_DIR"

echo "Downloading RFdiffusion model weights..."
echo "This will download ~8-10 GB of data. Please be patient."
echo ""

# ==========================================
# 1. Base_ckpt.pt (De novo monomer design)
# ==========================================
echo "[1/3] Downloading Base_ckpt.pt..."
if [ ! -f "$MODELS_DIR/Base_ckpt.pt" ]; then
    wget -q --show-progress \
        http://files.ipd.uw.edu/pub/RFdiffusion/6f5902ac237024bdd0c176cb93063dc4/Base_ckpt.pt \
        -O "$MODELS_DIR/Base_ckpt.pt"
    echo "✓ Base_ckpt.pt downloaded (de novo monomer design)"
else
    echo "✓ Base_ckpt.pt already exists"
fi
echo ""

# ==========================================
# 2. ActiveSite_ckpt.pt (Enzyme active sites)
# ==========================================
echo "[2/3] Downloading ActiveSite_ckpt.pt..."
if [ ! -f "$MODELS_DIR/ActiveSite_ckpt.pt" ]; then
    wget -q --show-progress \
        http://files.ipd.uw.edu/pub/RFdiffusion/5532d2e1f3a4738decd58b19d633b3c3/ActiveSite_ckpt.pt \
        -O "$MODELS_DIR/ActiveSite_ckpt.pt"
    echo "✓ ActiveSite_ckpt.pt downloaded (enzyme active site scaffolding)"
else
    echo "✓ ActiveSite_ckpt.pt already exists"
fi
echo ""

# ==========================================
# 3. Complex_beta_ckpt.pt (Protein complexes)
# ==========================================
echo "[3/3] Setting up Complex_beta_ckpt.pt (protein-protein complexes)..."
mkdir -p "$MODELS_DIR/Complex_beta_ckpt.pt"

# Download Base_ckpt.pt for Complex_beta if not already in the directory
if [ ! -f "$MODELS_DIR/Complex_beta_ckpt.pt/Base_ckpt.pt" ]; then
    if [ -f "$MODELS_DIR/Base_ckpt.pt" ]; then
        echo "  Copying Base_ckpt.pt to Complex_beta_ckpt.pt/..."
        cp "$MODELS_DIR/Base_ckpt.pt" "$MODELS_DIR/Complex_beta_ckpt.pt/Base_ckpt.pt"
    else
        echo "  Downloading Base_ckpt.pt for Complex_beta..."
        wget -q --show-progress \
            http://files.ipd.uw.edu/pub/RFdiffusion/6f5902ac237024bdd0c176cb93063dc4/Base_ckpt.pt \
            -O "$MODELS_DIR/Complex_beta_ckpt.pt/Base_ckpt.pt"
    fi
    echo "  ✓ Base_ckpt.pt ready"
else
    echo "  ✓ Base_ckpt.pt already exists"
fi

# Download Complex_base_ckpt.pt
if [ ! -f "$MODELS_DIR/Complex_beta_ckpt.pt/Complex_base_ckpt.pt" ]; then
    echo "  Downloading Complex_base_ckpt.pt..."
    wget -q --show-progress \
        http://files.ipd.uw.edu/pub/RFdiffusion/e29311f6f1bf1af907f9ef9f44b8328b/Complex_base_ckpt.pt \
        -O "$MODELS_DIR/Complex_beta_ckpt.pt/Complex_base_ckpt.pt"
    echo "  ✓ Complex_base_ckpt.pt downloaded"
else
    echo "  ✓ Complex_base_ckpt.pt already exists"
fi

# Download Complex_beta_ckpt.pt (the main beta model)
if [ ! -f "$MODELS_DIR/Complex_beta_ckpt.pt/Complex_beta_ckpt.pt" ]; then
    echo "  Downloading Complex_beta_ckpt.pt (beta model)..."
    wget -q --show-progress \
        http://files.ipd.uw.edu/pub/RFdiffusion/f572d396fae9206628714fb2ce00f72e/Complex_beta_ckpt.pt \
        -O "$MODELS_DIR/Complex_beta_ckpt.pt/Complex_beta_ckpt.pt"
    echo "  ✓ Complex_beta_ckpt.pt downloaded (generates diverse topologies)"
else
    echo "  ✓ Complex_beta_ckpt.pt already exists"
fi

echo ""
echo "========================================="
echo "✓ All RFdiffusion models downloaded!"
echo "========================================="
echo ""
echo "Models available:"
echo "  1. Base_ckpt.pt                    - De novo monomer design"
echo "  2. ActiveSite_ckpt.pt              - Enzyme active site scaffolding"
echo "  3. Complex_beta_ckpt.pt/           - Protein-protein complexes (binder design)"
echo ""
echo "Model directory structure:"
ls -lh "$MODELS_DIR/"
echo ""
ls -lh "$MODELS_DIR/Complex_beta_ckpt.pt/" 2>/dev/null || true
echo ""
echo "You can now run RFdiffusion with these models!"
