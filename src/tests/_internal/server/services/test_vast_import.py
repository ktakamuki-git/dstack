from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.core.models.instances import InstanceStatus
from dstack._internal.core.models.profiles import CreationPolicy, TerminationPolicy
from dstack._internal.server.models import FleetModel, InstanceModel
from dstack._internal.server.services import vast_import
from dstack._internal.server.services.external_runner import (
    get_external_runner_provider_instance_id,
    is_external_runner,
)
from dstack._internal.server.services.fleets import get_fleet_spec
from dstack._internal.server.services.instances import get_instance_provisioning_data
from dstack._internal.server.testing.common import (
    create_project,
    create_user,
    get_job_provisioning_data,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
async def test_import_vast_instance_registers_reuse_only_external_capacity(
    test_db,
    session: AsyncSession,
):
    user = await create_user(session=session)
    project = await create_project(session=session, owner=user)
    instance_type = get_job_provisioning_data(gpu_count=1).instance_type
    provider_instance = {
        "actual_status": "running",
        "ssh_host": "203.0.113.10",
        "ssh_port": 2222,
        "geolocation": "JP Japan",
        "dph_total": 0.42,
        "machine_id": 777,
    }
    api_client = Mock()
    api_client.get_instance.return_value = provider_instance
    api_client.attach_ssh_key.return_value = None
    pipeline_hinter = Mock()

    with (
        patch.object(
            vast_import.backends_services,
            "get_backend_config",
            AsyncMock(return_value=SimpleNamespace(creds=SimpleNamespace(api_key="test-key"))),
        ),
        patch.object(vast_import, "VastAIAPIClient", return_value=api_client),
        patch.object(
            vast_import,
            "_probe_instance_type_with_retry",
            Mock(return_value=instance_type),
        ),
        patch.object(
            vast_import.vast_preferences_services,
            "add_preferred_machine",
            AsyncMock(return_value=[777]),
        ) as add_preferred_machine,
    ):
        fleet = await vast_import.import_vast_instance(
            session=session,
            user=user,
            project=project,
            instance_id=12345,
            fleet_name="manual-vast",
            pipeline_hinter=pipeline_hinter,
        )

    api_client.get_instance.assert_called_once_with(12345)
    api_client.attach_ssh_key.assert_called_once_with(12345, project.ssh_public_key.strip())
    add_preferred_machine.assert_awaited_once_with(
        session=session, project=project, machine_id=777
    )
    assert fleet.name == "manual-vast"

    fleet_model = (
        await session.execute(select(FleetModel).where(FleetModel.id == fleet.id))
    ).scalar_one()
    fleet_spec = get_fleet_spec(fleet_model)
    assert fleet_spec.merged_profile.creation_policy == CreationPolicy.REUSE
    assert fleet_spec.configuration.nodes is not None
    assert fleet_spec.configuration.nodes.min == 0
    assert fleet_spec.configuration.nodes.target == 0
    assert fleet_spec.configuration.nodes.max == 1

    instance_model = (
        await session.execute(
            select(InstanceModel).where(InstanceModel.fleet_id == fleet_model.id)
        )
    ).scalar_one()
    jpd = get_instance_provisioning_data(instance_model)
    assert jpd is not None
    assert instance_model.status == InstanceStatus.IDLE
    assert instance_model.backend == BackendType.VASTAI
    assert instance_model.termination_policy == TerminationPolicy.DONT_DESTROY
    assert jpd.backend == BackendType.REMOTE
    assert jpd.base_backend == BackendType.VASTAI
    assert jpd.instance_id == "12345"
    assert jpd.hostname == "203.0.113.10"
    assert jpd.ssh_port == 2222
    assert is_external_runner(jpd)
    assert get_external_runner_provider_instance_id(jpd) == "12345"


@pytest.mark.asyncio
@pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
async def test_import_vast_instance_rejects_non_running_contract_before_registration(
    test_db,
    session: AsyncSession,
):
    user = await create_user(session=session)
    project = await create_project(session=session, owner=user)
    api_client = Mock()
    api_client.get_instance.return_value = {
        "actual_status": "stopped",
        "ssh_host": "203.0.113.10",
        "ssh_port": 2222,
    }

    with (
        patch.object(
            vast_import.backends_services,
            "get_backend_config",
            AsyncMock(return_value=SimpleNamespace(creds=SimpleNamespace(api_key="test-key"))),
        ),
        patch.object(vast_import, "VastAIAPIClient", return_value=api_client),
        pytest.raises(Exception, match="not running"),
    ):
        await vast_import.import_vast_instance(
            session=session,
            user=user,
            project=project,
            instance_id=12345,
            fleet_name="manual-vast",
            pipeline_hinter=Mock(),
        )

    api_client.attach_ssh_key.assert_not_called()
    assert (await session.execute(select(FleetModel))).scalars().all() == []
