import pytest

from examples.repair_recovery import scenario as recovery
from examples.sandbox_matrix import scenario
from runtime.tools.docker_backend import DockerBackend
from tests.docker.test_backend import require_real_docker

pytestmark = pytest.mark.docker


@pytest.mark.parametrize("kind", ["normal", "timeout", "memory", "cancel", "process_kill_recovery"])
async def test_real_lifecycle_matrix(kind):
    await require_real_docker(DockerBackend())
    assert (await scenario(kind))["status"] == "PASS"


@pytest.mark.parametrize("point", ["patch_committed", "verification_committed", "test_running", "graceful_stop"])
async def test_real_repair_process_boundaries(point):
    await require_real_docker(DockerBackend())
    assert (await recovery(point))["status"] == "PASS"
