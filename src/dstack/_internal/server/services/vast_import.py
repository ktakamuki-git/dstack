import re
import time
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dstack._internal.core.backends.vastai.api_client import VastAIAPIClient, VastAIRateLimitError
from dstack._internal.core.errors import ResourceExistsError, ServerClientError
from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.core.models.fleets import (
    Fleet,
    FleetConfiguration,
    FleetNodesSpec,
    FleetSpec,
)
from dstack._internal.core.models.instances import (
    InstanceAvailability,
    InstanceOfferWithAvailability,
    InstanceRuntime,
    InstanceStatus,
)
from dstack._internal.core.models.profiles import (
    CreationPolicy,
    Profile,
    SpotPolicy,
    TerminationPolicy,
)
from dstack._internal.core.models.runs import JobProvisioningData
from dstack._internal.core.services import validate_dstack_resource_name
from dstack._internal.server.models import InstanceModel, ProjectModel, UserModel
from dstack._internal.server.services import backends as backends_services
from dstack._internal.server.services import events
from dstack._internal.server.services import fleets as fleets_services
from dstack._internal.server.services import instances as instances_services
from dstack._internal.server.services import vast_preferences as vast_preferences_services
from dstack._internal.server.services.external_runner import (
    build_external_runner_backend_data,
    get_external_runner_provider_instance_id,
)
from dstack._internal.server.services.pipelines import PipelineHinterProtocol
from dstack._internal.server.services.ssh_fleets.provisioning import (
    detect_cpu_arch,
    get_paramiko_connection,
    host_info_to_instance_type,
)
from dstack._internal.utils.common import EntityName, get_current_datetime, run_async
from dstack._internal.utils.ssh import pkey_from_str


async def import_vast_instance(
    session: AsyncSession,
    user: UserModel,
    project: ProjectModel,
    instance_id: int,
    fleet_name: Optional[str],
    pipeline_hinter: PipelineHinterProtocol,
) -> Fleet:
    # Importing an external host gives dstack remote execution access, so use the
    # same permission gate as SSH fleets.
    fleets_services._check_can_manage_ssh_fleets(user=user, project=project)

    backend_config = await backends_services.get_backend_config(
        project=project, backend_type=BackendType.VASTAI
    )
    if backend_config is None:
        raise ServerClientError("Vast.ai backend is not configured for this project")
    creds = getattr(backend_config, "creds", None)
    api_key = getattr(creds, "api_key", None)
    if not api_key:
        raise ServerClientError("Vast.ai backend credentials are unavailable")

    name = fleet_name or f"vast-{instance_id}"
    validate_dstack_resource_name(name)
    await _check_not_already_imported(session=session, instance_id=instance_id)

    client = VastAIAPIClient(api_key=api_key)
    try:
        provider_instance = await run_async(client.get_instance, instance_id)
    except VastAIRateLimitError as e:
        raise ServerClientError("Vast.ai rate limit reached. Try again shortly") from e
    except Exception as e:
        raise ServerClientError(f"Failed to query Vast.ai instance {instance_id}: {e}") from e

    if not provider_instance:
        raise ServerClientError(f"Vast.ai instance {instance_id} was not found")

    machine_id = _get_machine_id(provider_instance)
    status = str(provider_instance.get("actual_status") or "unknown").lower()
    if status != "running":
        raise ServerClientError(
            f"Vast.ai instance {instance_id} is not running (status: {status})"
        )

    hostname, port = _get_ssh_endpoint(provider_instance, instance_id)

    try:
        await run_async(client.attach_ssh_key, instance_id, project.ssh_public_key.strip())
    except VastAIRateLimitError as e:
        raise ServerClientError("Vast.ai rate limit reached while attaching the SSH key") from e
    except Exception as e:
        raise ServerClientError(
            f"Failed to attach the dstack SSH key to Vast.ai instance {instance_id}: {e}"
        ) from e

    try:
        instance_type = await run_async(
            _probe_instance_type_with_retry,
            hostname,
            port,
            project.ssh_private_key,
            instance_id,
        )
    except Exception as e:
        raise ServerClientError(
            f"Could not connect to Vast.ai instance {instance_id} over SSH: {e}"
        ) from e

    region = _get_region(provider_instance)
    price = _get_price(provider_instance)
    spec = FleetSpec(
        configuration=FleetConfiguration(
            name=name,
            nodes=FleetNodesSpec(min=0, target=0, max=1),
            backends=[BackendType.VASTAI],
            spot_policy=SpotPolicy.AUTO,
            idle_duration=-1,
        ),
        profile=Profile(creation_policy=CreationPolicy.REUSE),
    )

    try:
        await fleets_services.create_fleet(
            session=session,
            project=project,
            user=user,
            spec=spec,
            pipeline_hinter=pipeline_hinter,
        )
    except ResourceExistsError:
        raise ServerClientError(f"Fleet {name!r} already exists")

    fleet_model = await fleets_services.get_project_fleet_model_by_name(
        session=session,
        project=project,
        name=name,
    )
    if fleet_model is None:
        raise RuntimeError("Imported Vast.ai fleet disappeared after creation")

    backend_data = build_external_runner_backend_data(str(instance_id))
    jpd = JobProvisioningData(
        backend=BackendType.REMOTE,
        base_backend=BackendType.VASTAI,
        instance_type=instance_type,
        instance_id=str(instance_id),
        hostname=hostname,
        region=region,
        price=price,
        username="root",
        ssh_port=port,
        dockerized=False,
        backend_data=backend_data,
    )
    offer = InstanceOfferWithAvailability(
        backend=BackendType.VASTAI,
        instance=instance_type,
        region=region,
        price=price,
        availability=InstanceAvailability.IDLE,
        instance_runtime=InstanceRuntime.RUNNER,
    )
    now = get_current_datetime()
    instance_model = InstanceModel(
        id=uuid.uuid4(),
        name=f"{name}-0"[:50],
        instance_num=0,
        project=project,
        fleet=fleet_model,
        backend=BackendType.VASTAI,
        backend_data=backend_data,
        created_at=now,
        started_at=now,
        status=InstanceStatus.IDLE,
        unreachable=False,
        job_provisioning_data=jpd.model_dump_json(),
        offer=offer.model_dump_json(),
        region=region,
        price=price,
        termination_policy=TerminationPolicy.DONT_DESTROY,
        termination_idle_time=-1,
        total_blocks=1,
        busy_blocks=0,
    )
    session.add(instance_model)
    if machine_id is not None:
        await vast_preferences_services.add_preferred_machine(
            session=session, project=project, machine_id=machine_id
        )
    events.emit(
        session=session,
        message=(
            f"Imported existing Vast.ai instance {instance_id}. "
            + (f"Machine {machine_id} was added to preferred Vast.ai machines. " if machine_id else "")
            + "The provider instance remains externally owned and is not destroyed with the fleet."
        ),
        actor=events.UserActor.from_user(user),
        targets=[
            events.Target.from_model(fleet_model),
            events.Target.from_model(instance_model),
        ],
    )
    await session.commit()
    pipeline_hinter.hint_fetch(InstanceModel.__name__)

    fleet = await fleets_services.get_fleet(
        session=session,
        project=project,
        name_or_id=EntityName(name=name),
    )
    if fleet is None:
        raise RuntimeError("Failed to load imported Vast.ai fleet")
    return fleet


async def _check_not_already_imported(
    session: AsyncSession,
    instance_id: int,
) -> None:
    res = await session.execute(
        select(InstanceModel).where(
            InstanceModel.deleted == False,
            InstanceModel.backend == BackendType.VASTAI,
        )
    )
    wanted = str(instance_id)
    for instance_model in res.scalars().all():
        jpd = instances_services.get_instance_provisioning_data(instance_model)
        if get_external_runner_provider_instance_id(jpd) == wanted:
            raise ServerClientError(f"Vast.ai instance {instance_id} is already imported")


def _get_machine_id(provider_instance: dict) -> Optional[int]:
    raw = provider_instance.get("machine_id")
    try:
        machine_id = int(raw)
    except (TypeError, ValueError):
        return None
    return machine_id if machine_id > 0 else None


def _get_ssh_endpoint(provider_instance: dict, instance_id: int) -> tuple[str, int]:
    hostname = provider_instance.get("ssh_host") or provider_instance.get("public_ipaddr")
    port = provider_instance.get("ssh_port")
    if not port:
        ports = provider_instance.get("ports") or {}
        ssh_ports = ports.get("22/tcp") or []
        if ssh_ports:
            port = ssh_ports[0].get("HostPort")
    if not hostname or not port:
        raise ServerClientError(
            f"Vast.ai instance {instance_id} does not expose a ready SSH endpoint"
        )
    try:
        return str(hostname).strip(), int(port)
    except (TypeError, ValueError) as e:
        raise ServerClientError(
            f"Vast.ai instance {instance_id} returned an invalid SSH endpoint"
        ) from e


def _probe_instance_type_with_retry(
    hostname: str,
    port: int,
    private_key: str,
    instance_id: int,
):
    last_error: Optional[Exception] = None
    for attempt in range(20):
        try:
            return _probe_instance_type(hostname, port, private_key, instance_id)
        except Exception as e:
            last_error = e
            if attempt < 19:
                time.sleep(2)
    assert last_error is not None
    raise last_error


def _probe_instance_type(hostname: str, port: int, private_key: str, instance_id: int):
    pkey = pkey_from_str(private_key)
    with get_paramiko_connection("root", hostname, port, [pkey]) as client:
        arch = detect_cpu_arch(client)
        cpus = int(_remote_output(client, "getconf _NPROCESSORS_ONLN || nproc"))
        memory_bytes = int(
            _remote_output(
                client,
                "awk '/MemTotal:/ {printf \"%.0f\", $2 * 1024}' /proc/meminfo",
            )
        )
        disk_bytes = int(_remote_output(client, "df -P -B1 / | awk 'NR==2 {print $2}'"))
        gpu_output = _remote_output(
            client,
            (
                "if command -v nvidia-smi >/dev/null 2>&1; then "
                "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits; fi"
            ),
            allow_empty=True,
        )
        gpu_lines = [line.strip() for line in gpu_output.splitlines() if line.strip()]
        host_info = {
            "cpus": cpus,
            "memory": memory_bytes,
            "disk_size": disk_bytes,
            "gpu_count": len(gpu_lines),
        }
        if gpu_lines:
            gpu_name, gpu_memory = [part.strip() for part in gpu_lines[0].rsplit(",", 1)]
            host_info.update(
                {
                    "gpu_vendor": "nvidia",
                    "gpu_name": gpu_name,
                    "gpu_memory": int(float(gpu_memory)),
                }
            )
        instance_type = host_info_to_instance_type(host_info, arch)
        instance_type.name = f"vast-{instance_id}"
        return instance_type


def _remote_output(client, command: str, allow_empty: bool = False) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=20)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(err or out or f"Remote command failed with status {status}")
    if not out and not allow_empty:
        raise RuntimeError(f"Remote command returned no output: {command}")
    return out


def _get_region(provider_instance: dict) -> str:
    raw = (
        provider_instance.get("geolocation")
        or provider_instance.get("location")
        or provider_instance.get("country")
        or "vastai-imported"
    )
    value = re.sub(r"[^a-z0-9]+", "-", str(raw).lower()).strip("-")
    return value or "vastai-imported"


def _get_price(provider_instance: dict) -> float:
    for key in ("dph_total", "actual_cost", "cost"):
        value = provider_instance.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return 0.0
