from typing import Annotated, List, Literal, Optional, Union

from pydantic import Field, model_validator
from typing_extensions import Self

from dstack._internal.core.backends.base.models import fill_data
from dstack._internal.core.models.common import CoreModel

# TODO: Re-evaluate this default once Vast Server Cloud inventory improves for
# CUDA-sensitive GPU families (e.g. H100 with strict cuda_max_good filtering).
VASTAI_COMMUNITY_CLOUD_DEFAULT = True


class VastAIAPIKeyCreds(CoreModel):
    type: Annotated[Literal["api_key"], Field(description="The type of credentials")] = "api_key"
    api_key: Annotated[str, Field(description="The API key")]


AnyVastAICreds = VastAIAPIKeyCreds
VastAICreds = AnyVastAICreds


class VastAIAPIKeyFileCreds(CoreModel):
    type: Annotated[Literal["api_key"], Field(description="The type of credentials")] = "api_key"
    filename: Annotated[
        str, Field(description="The path to the Vast.ai API key file", exclude=True)
    ]
    api_key: Annotated[
        Optional[str],
        Field(
            description=(
                "The Vast.ai API key."
                " When configuring via server/config.yml, it's automatically filled from filename."
                " When configuring via UI, it has to be specified explicitly"
            )
        ),
    ] = None

    @model_validator(mode="after")
    def fill_api_key(self) -> Self:
        return fill_data(self, filename_field="filename", data_field="api_key")


AnyVastAIFileCreds = VastAIAPIKeyFileCreds


class VastAIBackendConfig(CoreModel):
    type: Annotated[Literal["vastai"], Field(description="The type of backend")] = "vastai"
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of VastAI regions. Omit to use all regions"),
    ] = None
    community_cloud: Annotated[
        Optional[bool],
        Field(
            description=(
                "Whether Community Cloud offers can be suggested in addition to Server Cloud."
                f" Defaults to `{str(VASTAI_COMMUNITY_CLOUD_DEFAULT).lower()}`"
            )
        ),
    ] = None


class VastAIBackendConfigWithCreds(VastAIBackendConfig):
    creds: Annotated[AnyVastAICreds, Field(description="The credentials")]


class VastAIBackendFileConfigWithCreds(VastAIBackendConfig):
    creds: Annotated[AnyVastAIFileCreds, Field(description="The credentials")]


AnyVastAIBackendConfig = Union[VastAIBackendConfig, VastAIBackendConfigWithCreds]


class VastAIStoredConfig(VastAIBackendConfig):
    pass


class VastAIConfig(VastAIStoredConfig):
    creds: AnyVastAICreds
    # Runtime preference populated from BackendModel.preferences. It is intentionally
    # separate from VastAIStoredConfig so config.yml updates cannot erase it.
    preferred_machine_ids: List[Annotated[int, Field(gt=0)]] = Field(default_factory=list)

    @property
    def allow_community_cloud(self) -> bool:
        if self.community_cloud is not None:
            return self.community_cloud
        return VASTAI_COMMUNITY_CLOUD_DEFAULT
