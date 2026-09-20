from types import SimpleNamespace
from typing import Optional, Union
from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dstack._internal.core.backends.base.backend import Backend
from dstack._internal.core.errors import ServerClientError
from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.core.models.fleets import (
    FleetConfiguration,
    FleetNodesSpec,
    FleetSpec,
    InstanceGroupPlacement,
    SSHHostParams,
    SSHParams,
)
from dstack._internal.core.models.instances import RemoteConnectionInfo
from dstack._internal.core.models.users import GlobalRole
from dstack._internal.server.models import FleetModel, ProjectModel
from dstack._internal.server.services import fleets as fleets_services
from dstack._internal.server.services.backends import get_project_backends
from dstack._internal.server.services.fleets import (
    get_fleet_master_instance_provisioning_data,
    get_plan,
)
from dstack._internal.server.testing.common import (
    create_fleet,
    create_instance,
    create_project,
    create_user,
    get_fleet_spec,
    get_job_provisioning_data,
    get_ssh_key,
)


class TestRegisterVastInstance:
    @pytest.mark.asyncio
    async def test_registers_rented_instance_as_ssh_fleet(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await create_user(session=session, global_role=GlobalRole.ADMIN)
        project = await create_project(session=session, owner=user)
        calls = {}

        async def get_backend_config(*, project, backend_type):
            assert backend_type == BackendType.VASTAI
            return SimpleNamespace(creds=SimpleNamespace(api_key="test-api-key"))

        class FakeVastAIAPIClient:
            def __init__(self, api_key: str):
                assert api_key == "test-api-key"

            def get_instance(self, instance_id: int):
                assert instance_id == 51729340
                return {
                    "actual_status": "running",
                    "ssh_host": "203.0.113.42",
                    "ssh_port": 23456,
                }

            def attach_ssh_key(self, instance_id: int, ssh_key: str):
                calls["attached"] = (instance_id, ssh_key)

        async def get_plan(*, session, project, user, spec):
            calls["spec"] = spec
            return SimpleNamespace(current_resource=None)

        sentinel = object()

        async def apply_plan(**kwargs):
            calls["apply"] = kwargs
            return sentinel

        monkeypatch.setattr(fleets_services.backends_services, "get_backend_config", get_backend_config)
        monkeypatch.setattr(fleets_services, "VastAIAPIClient", FakeVastAIAPIClient)
        monkeypatch.setattr(fleets_services, "get_plan", get_plan)
        monkeypatch.setattr(fleets_services, "apply_plan", apply_plan)

        result = await fleets_services.register_vast_instance(
            session=session,
            user=user,
            project=project,
            instance_id=51729340,
            fleet_name=None,
            pipeline_hinter=Mock(),
        )

        assert result is sentinel
        spec = calls["spec"]
        assert spec.configuration.name == "vast-51729340"
        assert spec.configuration.ssh_config.user == "root"
        host = spec.configuration.ssh_config.hosts[0]
        assert isinstance(host, SSHHostParams)
        assert host.hostname == "203.0.113.42"
        assert host.port == 23456
        assert calls["attached"] == (51729340, project.ssh_public_key.strip())
        assert calls["apply"]["plan"].spec.configuration.name == "vast-51729340"

    @pytest.mark.asyncio
    async def test_errors_when_ssh_endpoint_is_not_ready(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await create_user(session=session, global_role=GlobalRole.ADMIN)
        project = await create_project(session=session, owner=user)

        async def get_backend_config(*, project, backend_type):
            return SimpleNamespace(creds=SimpleNamespace(api_key="test-api-key"))

        class FakeVastAIAPIClient:
            def __init__(self, api_key: str):
                pass

            def get_instance(self, instance_id: int):
                return {"actual_status": "loading"}

        monkeypatch.setattr(fleets_services.backends_services, "get_backend_config", get_backend_config)
        monkeypatch.setattr(fleets_services, "VastAIAPIClient", FakeVastAIAPIClient)

        with pytest.raises(ServerClientError, match="does not have a ready SSH endpoint"):
            await fleets_services.register_vast_instance(
                session=session,
                user=user,
                project=project,
                instance_id=51729340,
                fleet_name=None,
                pipeline_hinter=Mock(),
            )


class TestGetPlanSSHFleetHostsValidation:
    @pytest.fixture
    def get_project_backends_mock(self, monkeypatch: pytest.MonkeyPatch) -> list[Backend]:
        mock = Mock(spec_set=get_project_backends, return_value=[])
        monkeypatch.setattr("dstack._internal.server.services.backends.get_project_backends", mock)
        return mock

    def get_ssh_fleet_spec(
        self, name: Optional[str], hosts: list[Union[SSHHostParams, str]]
    ) -> FleetSpec:
        ssh_config = SSHParams(
            hosts=hosts,
            network=None,
            user="ubuntu",
            ssh_key=get_ssh_key(),
        )
        fleet_conf = FleetConfiguration(name=name, ssh_config=ssh_config)
        return get_fleet_spec(conf=fleet_conf)

    async def create_fleet(
        self, session: AsyncSession, project: ProjectModel, spec: FleetSpec
    ) -> FleetModel:
        assert spec.configuration.ssh_config is not None, spec.configuration
        fleet = await create_fleet(session=session, project=project, spec=spec)
        for host in spec.configuration.ssh_config.hosts:
            if isinstance(host, SSHHostParams):
                hostname = host.hostname
            else:
                hostname = host
            rci = RemoteConnectionInfo(host=hostname, port=22, ssh_user="admin", ssh_keys=[])
            await create_instance(
                session=session,
                project=project,
                fleet=fleet,
                backend=BackendType.REMOTE,
                remote_connection_info=rci,
            )
        return fleet

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
    @pytest.mark.usefixtures("test_db", "get_project_backends_mock")
    async def test_ok_same_fleet_update(self, session: AsyncSession):
        user = await create_user(session=session)
        project = await create_project(session=session, owner=user)
        old_fleet_spec = self.get_ssh_fleet_spec(name="my-fleet", hosts=["192.168.100.201"])
        await self.create_fleet(session, project, old_fleet_spec)
        new_fleet_spec = self.get_ssh_fleet_spec(
            name="my-fleet", hosts=["192.168.100.201", "192.168.100.202"]
        )
        plan = await get_plan(session=session, project=project, user=user, spec=new_fleet_spec)
        assert plan.current_resource is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
    @pytest.mark.usefixtures("test_db", "get_project_backends_mock")
    async def test_ok_deleted_instances_ignored(self, session: AsyncSession):
        user = await create_user(session=session)
        project = await create_project(session=session, owner=user)
        deleted_fleet_spec = self.get_ssh_fleet_spec(name="my-fleet", hosts=["192.168.100.201"])
        deleted_fleet = await self.create_fleet(session, project, deleted_fleet_spec)
        for instance in deleted_fleet.instances:
            instance.deleted = True
        deleted_fleet.deleted = True
        await session.commit()
        fleet_spec = self.get_ssh_fleet_spec(
            name="my-fleet", hosts=["192.168.100.201", "192.168.100.202"]
        )
        plan = await get_plan(session=session, project=project, user=user, spec=fleet_spec)
        assert plan.current_resource is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
    @pytest.mark.usefixtures("test_db", "get_project_backends_mock")
    async def test_ok_no_common_hosts_with_another_fleet(self, session: AsyncSession):
        user = await create_user(session=session)
        project = await create_project(session=session, owner=user)
        another_fleet_spec = self.get_ssh_fleet_spec(
            name="another-fleet", hosts=["192.168.100.201"]
        )
        await self.create_fleet(session, project, another_fleet_spec)
        fleet_spec = self.get_ssh_fleet_spec(name="new-fleet", hosts=["192.168.100.202"])
        plan = await get_plan(session=session, project=project, user=user, spec=fleet_spec)
        assert plan.current_resource is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
    @pytest.mark.usefixtures("test_db", "get_project_backends_mock")
    async def test_error_another_fleet_same_project(self, session: AsyncSession):
        user = await create_user(session=session)
        project = await create_project(session=session, owner=user)
        another_fleet_spec = self.get_ssh_fleet_spec(
            name="another-fleet", hosts=["192.168.100.201"]
        )
        await self.create_fleet(session, project, another_fleet_spec)
        fleet_spec = self.get_ssh_fleet_spec(
            name="new-fleet", hosts=["192.168.100.201", "192.168.100.202"]
        )
        with pytest.raises(
            ServerClientError, match=r"Instances \[192\.168\.100\.201\] are already assigned"
        ):
            await get_plan(session=session, project=project, user=user, spec=fleet_spec)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
    @pytest.mark.usefixtures("test_db", "get_project_backends_mock")
    async def test_error_another_fleet_another_project(self, session: AsyncSession):
        another_user = await create_user(session=session, name="another-user")
        another_project = await create_project(
            session=session, owner=another_user, name="another-project"
        )
        another_fleet_spec = self.get_ssh_fleet_spec(
            name="another-fleet", hosts=["192.168.100.201"]
        )
        await self.create_fleet(session, another_project, another_fleet_spec)
        user = await create_user(session=session, name="my-user")
        project = await create_project(session=session, owner=user, name="my-project")
        fleet_spec = self.get_ssh_fleet_spec(
            name="my-fleet", hosts=["192.168.100.201", "192.168.100.202"]
        )
        with pytest.raises(
            ServerClientError, match=r"Instances \[192\.168\.100\.201\] are already assigned"
        ):
            await get_plan(session=session, project=project, user=user, spec=fleet_spec)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
    @pytest.mark.usefixtures("test_db", "get_project_backends_mock")
    async def test_error_fleet_spec_without_name(self, session: AsyncSession):
        # Even if the user apply the same configuration again, we cannot be sure if it is the same
        # fleet or a brand new fleet, as we identify fleets by name.
        user = await create_user(session=session)
        project = await create_project(session=session, owner=user)
        existing_fleet_spec = self.get_ssh_fleet_spec(
            name="autogenerated-fleet-name", hosts=["192.168.100.201"]
        )
        await self.create_fleet(session, project, existing_fleet_spec)
        fleet_spec_without_name = self.get_ssh_fleet_spec(name=None, hosts=["192.168.100.201"])
        with pytest.raises(
            ServerClientError, match=r"Instances \[192\.168\.100\.201\] are already assigned"
        ):
            await get_plan(
                session=session, project=project, user=user, spec=fleet_spec_without_name
            )


class TestGetFleetMasterInstanceProvisioningData:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
    async def test_returns_none_without_current_master_instance(
        self, test_db, session: AsyncSession
    ) -> None:
        project = await create_project(session=session)
        fleet_spec = get_fleet_spec()
        fleet_spec.configuration.placement = InstanceGroupPlacement.CLUSTER
        fleet_spec.configuration.nodes = FleetNodesSpec(min=0, target=1, max=2)
        fleet = await create_fleet(session=session, project=project, spec=fleet_spec)
        await create_instance(
            session=session,
            project=project,
            fleet=fleet,
            job_provisioning_data=get_job_provisioning_data(region="eu-west-1"),
        )

        master_provisioning_data = get_fleet_master_instance_provisioning_data(
            fleet_model=fleet,
            fleet_spec=fleet_spec,
        )

        assert master_provisioning_data is None
