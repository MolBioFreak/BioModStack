"""Opt-in CPU transport test environment; never auto-loaded by product code."""
import os
from pathlib import Path


def pytest_configure(config):
    jar = Path('/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')
    if jar.is_file():
        os.environ.setdefault('BMS_TEST_NEXTFLOW_JAR', str(jar))
    os.environ.setdefault('JAVA_TOOL_OPTIONS', '-XX:ActiveProcessorCount=2')
