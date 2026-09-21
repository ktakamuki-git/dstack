import json
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dstack._internal.core.backends.vastai.backend import VastAIBackend
from dstack._internal.core.backends.vastai.configurator import VastAIConfigurator
from dstack._internal.core.backends.vastai.models import (
    VastAIBackendConfigWithCreds,
    VastAIConfig,
    VastAICreds,
)
from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.server.models import BackendModel, DecryptedString
from dstack._internal.server.services import backends as backends_services
from dstack._internal.server.services import vast_preferences
from dstack._internal.server.testing.common import create_project, create_user


@pytest.mark.asyncio
@pytest.mark.parametrize("test_db", ["sqlite", "postgres"], indirect=True)
async def test_vast_preferred_machine_ids_persist_separately_from_backend_config(
    test_db, session: AsyncSession
):
    user = await create_user(session=session)
    project = await create_project(session=session, owner=user)
    backend_model = BackendModel(
        project_id=project.id,
        project=project,
        type=BackendType.VASTAI,
        config='{"type":"vastai","regions":[]}',
        auth=DecryptedString(plaintext='{"type":"api_key","api_key":"test"}'),
    )
    session.add(backend_model)
    await session.commit()
    await session.refresh(project, attribute_names=["backends"])

    assert await vast_preferences.add_preferred_machine(session, project, 901) == [901]
    assert await vast_preferences.add_preferred_machine(session, project, 902) == [901, 902]
    assert await vast_preferences.add_preferred_machine(session, project, 901) == [901, 902]
    await session.commit()

    assert json.loads(backend_model.preferences or "{}") == {
        "preferred_machine_ids": [901, 902]
    }
    assert backend_model.config == '{"type":"vastai","regions":[]}'
    assert await vast_preferences.list_preferred_machine_ids(project) == [901, 902]

    runtime_backend = await backends_services.get_project_backend_by_type(
        project=project, backend_type=BackendType.VASTAI
    )
    assert isinstance(runtime_backend, VastAIBackend)
    assert runtime_backend.compute().config.preferred_machine_ids == [901, 902]

    with patch.object(VastAIConfigurator, "validate_config", return_value=None):
        await backends_services.update_backend(
            session=session,
            project=project,
            config=VastAIBackendConfigWithCreds(
                regions=["us"],
                creds=VastAICreds(api_key="replacement-key"),
            ),
        )
    await session.commit()
    await session.refresh(backend_model)
    assert json.loads(backend_model.preferences or "{}") == {
        "preferred_machine_ids": [901, 902]
    }

    assert await vast_preferences.remove_preferred_machine(session, project, 901) == [902]


def test_vast_backend_preferences_are_applied_to_runtime_compute():
    backend_model = BackendModel(
        type=BackendType.VASTAI,
        project_id=__import__("uuid").uuid4(),
        config="{}",
        auth=DecryptedString(plaintext="{}"),
        preferences='{"preferred_machine_ids":[901,"902",901,-1,"bad"]}',
    )
    backend = VastAIBackend(VastAIConfig(creds=VastAICreds(api_key="test")))

    backends_services._apply_backend_preferences(backend_model, backend)

    assert backend.config.preferred_machine_ids == [901, 902]
    assert backend.compute().config.preferred_machine_ids == [901, 902]
