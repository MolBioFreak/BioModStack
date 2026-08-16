FROM nvidia/cuda:13.0.2-runtime-ubuntu24.04

ARG OPENMM_VERSION=8.5.2
ARG MAMBA_VERSION=2.5.0

LABEL org.opencontainers.image.title="BioModStack OpenMM MD"
LABEL org.opencontainers.image.version="${OPENMM_VERSION}"
LABEL org.opencontainers.image.description="Experimental CUDA-only OpenMM replica engine"

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/conda/bin:${PATH} \
    OPENMM_DEFAULT_PLATFORM=CUDA \
    OPENMM_GROMACS_INCLUDE_DIR=/opt/conda/share/gromacs/top \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl bzip2 \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://micro.mamba.pm/api/micromamba/linux-64/${MAMBA_VERSION}" \
      | tar -xj -C /usr/local/bin --strip-components=1 bin/micromamba \
    && micromamba create -y -p /opt/conda -c conda-forge \
         "python=3.11" "openmm=${OPENMM_VERSION}" "cuda-version=13.0" "gromacs=2025.3" \
    && micromamba clean --all --yes \
    && python -c "import openmm; assert openmm.__version__ == '${OPENMM_VERSION}'"

WORKDIR /workspace
CMD ["python", "-c", "import openmm; from openmm import Platform; names=[Platform.getPlatform(i).getName() for i in range(Platform.getNumPlatforms())]; print(openmm.__version__, names); assert 'CUDA' in names"]
