import json

from sqlalchemy.ext.asyncio import AsyncSession

from dstack._internal.core.errors import ServerClientError
from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.server.models import BackendModel, ProjectModel
from dstack._internal.server.services import backends as backends_services

_KEY = "preferred_machine_ids"


def _parse_preferences(backend_model: BackendModel) -> dict:
    if backend_model.preferences is None:
        return {}
    try:
        value = json.loads(backend_model.preferences)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _normalize_ids(raw_ids) -> list[int]:
    ids: list[int] = []
    if not isinstance(raw_ids, list):
        return ids
    for raw_id in raw_ids:
        try:
            machine_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if machine_id > 0 and machine_id not in ids:
            ids.append(machine_id)
    return ids


async def list_preferred_machine_ids(project: ProjectModel) -> list[int]:
    backend_model = await backends_services.get_project_backend_model_by_type(
        project=project, backend_type=BackendType.VASTAI
    )
    if backend_model is None:
        return []
    return _normalize_ids(_parse_preferences(backend_model).get(_KEY, []))


async def add_preferred_machine(
    session: AsyncSession, project: ProjectModel, machine_id: int
) -> list[int]:
    return await _update_preferred_machines(session, project, machine_id, remove=False)


async def remove_preferred_machine(
    session: AsyncSession, project: ProjectModel, machine_id: int
) -> list[int]:
    return await _update_preferred_machines(session, project, machine_id, remove=True)


async def _update_preferred_machines(
    session: AsyncSession, project: ProjectModel, machine_id: int, remove: bool
) -> list[int]:
    if machine_id <= 0:
        raise ServerClientError("Vast.ai machine ID must be positive")
    backend_model = await backends_services.get_project_backend_model_by_type(
        project=project, backend_type=BackendType.VASTAI
    )
    if backend_model is None:
        raise ServerClientError("Vast.ai backend is not configured for this project")

    preferences = _parse_preferences(backend_model)
    ids = _normalize_ids(preferences.get(_KEY, []))
    if remove:
        ids = [value for value in ids if value != machine_id]
    elif machine_id not in ids:
        ids.append(machine_id)
    preferences[_KEY] = ids
    backend_model.preferences = json.dumps(preferences, separators=(",", ":"))
    session.add(backend_model)
    await session.flush()
    await backends_services.invalidate_project_backend_cache(project.id)
    return ids
