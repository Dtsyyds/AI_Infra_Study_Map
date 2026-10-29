"""Worker CLI observability must survive an imported SDK's root logger setup."""

import os
import subprocess
import sys


def test_worker_cli_emits_cleanup_evidence_after_dependency_logging_setup():
    code = """
import logging
import sys
from runtime.orchestrator import worker

logging.basicConfig(level=logging.ERROR, force=True)
async def probe(*args):
    logging.info('ARC_REF_STARTUP_CLEANUP observable')
worker.main_async = probe
sys.argv = ['worker', '--once']
worker.main()
"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "ARC_REF_STARTUP_CLEANUP observable" in result.stderr.splitlines()
