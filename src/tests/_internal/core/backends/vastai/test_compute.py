from unittest.mock import MagicMock, patch

from dstack._internal.core.backends.vastai.compute import VastAICompute
from dstack._internal.core.backends.vastai.models import VastAIConfig, VastAICreds
from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.core.models.instances import (
    Disk,
    Gpu,
    InstanceAvailability,
    InstanceOffer,
    InstanceOfferWithAvailability,
    InstanceType,
    Resources,
)
from dstack._internal.core.models.resources import ResourcesSpec
from dstack._internal.core.models.runs import Requirements


def _config(community_cloud=None, preferred_machine_ids=None) -> VastAIConfig:
    return VastAIConfig(
        creds=VastAICreds(api_key="test"),
        community_cloud=community_cloud,
        preferred_machine_ids=preferred_machine_ids or [],
    )


def _requirements() -> Requirements:
    return Requirements(resources=ResourcesSpec())


def _offer(
    *, spot: bool, price: float = 0.5, min_bid: float | None = None
) -> InstanceOfferWithAvailability:
    return InstanceOfferWithAvailability(
        backend=BackendType.VASTAI,
        instance=InstanceType(
            name="12345",
            resources=Resources(
                cpus=8,
                memory_mib=32 * 1024,
                gpus=[Gpu(name="RTX4090", memory_mib=24 * 1024)],
                spot=spot,
                disk=Disk(size_mib=100 * 1024),
            ),
        ),
        region="Hong Kong, HK",
        price=price,
        availability=InstanceAvailability.AVAILABLE,
        backend_data={
            **({"min_bid": min_bid} if min_bid is not None else {}),
        },
    )


def _catalog_offer(*, name: str, price: float) -> InstanceOffer:
    offer = _offer(spot=False, price=price)
    return InstanceOffer(
        backend=offer.backend,
        instance=offer.instance.model_copy(update={"name": name}),
        region=offer.region,
        price=offer.price,
        backend_data=offer.backend_data.copy(),
    )


# build_authorized_keys() rejects the project key unless it actually parses
PROJECT_SSH_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINOmx0T+hBRaJ6jCi21ZYe2NW3EZS8e0Mdwl+yZJt+kD project"
)


def _run_job(compute: VastAICompute, offer: InstanceOfferWithAvailability):
    run = MagicMock()
    job = MagicMock()
    job.job_spec.image_name = "dstackai/base:latest"
    job.job_spec.registry_auth = None
    with (
        patch(
            "dstack._internal.core.backends.vastai.compute.generate_unique_instance_name_for_job",
            return_value="dstack-test",
        ),
        patch(
            "dstack._internal.core.backends.vastai.compute.get_docker_commands",
            return_value=["echo hi"],
        ),
    ):
        compute.run_job(
            run=run,
            job=job,
            instance_offer=offer,
            project_ssh_public_key=PROJECT_SSH_PUBLIC_KEY,
            project_ssh_private_key="private-key",
            volumes=[],
            placement_group=None,
            requirements=_requirements(),
            extra_authorized_keys=[],
        )


def test_vastai_compute_enables_community_cloud_by_default():
    with (
        patch("dstack._internal.core.backends.vastai.compute.VastAIProvider") as vast_provider_cls,
        patch("dstack._internal.core.backends.vastai.compute.gpuhunt.Catalog") as catalog_cls,
        patch("dstack._internal.core.backends.vastai.compute.get_catalog_offers", return_value=[]),
    ):
        catalog_instance = catalog_cls.return_value
        compute = VastAICompute(_config())
        list(compute.get_offers(_requirements(), full_offers=False, unallocated_resources=False))
        vast_provider_cls.assert_called_once()
        assert vast_provider_cls.call_args.kwargs["community_cloud"] is True
        catalog_instance.add_provider.assert_called_once()


def test_vastai_compute_can_enable_community_cloud():
    with (
        patch("dstack._internal.core.backends.vastai.compute.VastAIProvider") as vast_provider_cls,
        patch("dstack._internal.core.backends.vastai.compute.gpuhunt.Catalog") as catalog_cls,
        patch("dstack._internal.core.backends.vastai.compute.get_catalog_offers", return_value=[]),
    ):
        catalog_instance = catalog_cls.return_value
        compute = VastAICompute(_config(community_cloud=True))
        list(compute.get_offers(_requirements(), full_offers=False, unallocated_resources=False))
        vast_provider_cls.assert_called_once()
        assert vast_provider_cls.call_args.kwargs["community_cloud"] is True
        catalog_instance.add_provider.assert_called_once()


def test_vastai_compute_can_disable_community_cloud():
    with (
        patch("dstack._internal.core.backends.vastai.compute.VastAIProvider") as vast_provider_cls,
        patch("dstack._internal.core.backends.vastai.compute.gpuhunt.Catalog") as catalog_cls,
        patch("dstack._internal.core.backends.vastai.compute.get_catalog_offers", return_value=[]),
    ):
        catalog_instance = catalog_cls.return_value
        compute = VastAICompute(_config(community_cloud=False))
        list(compute.get_offers(_requirements(), full_offers=False, unallocated_resources=False))
        vast_provider_cls.assert_called_once()
        assert vast_provider_cls.call_args.kwargs["community_cloud"] is False
        catalog_instance.add_provider.assert_called_once()


def test_vastai_run_job_bids_on_spot_offer():
    compute = VastAICompute(_config())
    compute.api_client = MagicMock()
    compute.api_client.create_instance.return_value = 123

    _run_job(compute, _offer(spot=True, price=0.14, min_bid=0.1244444))

    assert compute.api_client.create_instance.call_args.kwargs["bid"] == 0.1244444


def test_vastai_run_job_does_not_bid_on_ondemand_offer():
    compute = VastAICompute(_config())
    compute.api_client = MagicMock()
    compute.api_client.create_instance.return_value = 123

    _run_job(compute, _offer(spot=False, price=0.24))

    assert compute.api_client.create_instance.call_args.kwargs["bid"] is None


def test_vastai_compute_prioritizes_live_preferred_machine_offers():
    preferred = _catalog_offer(name="preferred-offer", price=0.50)
    marketplace_duplicate = preferred.model_copy(deep=True)
    marketplace_other = _catalog_offer(name="other-offer", price=0.20)

    with patch(
        "dstack._internal.core.backends.vastai.compute.get_catalog_offers",
        side_effect=[[preferred], [marketplace_other, marketplace_duplicate]],
    ) as get_catalog_offers:
        compute = VastAICompute(_config(preferred_machine_ids=[777]))
        offers = compute.get_offers_by_requirements(
            _requirements(), full_offers=False, unallocated_resources=False
        )

    assert [offer.instance.name for offer in offers] == ["preferred-offer", "other-offer"]
    assert offers[0].backend_data["preferred_machine_id"] == 777
    preferred_catalog = get_catalog_offers.call_args_list[0].kwargs["catalog"]
    provider = preferred_catalog.providers[0]
    assert provider.extra_filters["machine_id"] == {"eq": 777}


def test_vastai_compute_falls_back_when_preferred_machine_is_unavailable():
    marketplace = _catalog_offer(name="market-offer", price=0.25)

    with patch(
        "dstack._internal.core.backends.vastai.compute.get_catalog_offers",
        side_effect=[[], [marketplace]],
    ):
        compute = VastAICompute(_config(preferred_machine_ids=[777]))
        offers = compute.get_offers_by_requirements(
            _requirements(), full_offers=False, unallocated_resources=False
        )

    assert [offer.instance.name for offer in offers] == ["market-offer"]
    assert "preferred_machine_id" not in offers[0].backend_data


def test_vastai_compute_checks_each_preferred_machine_once_in_saved_order():
    first = _catalog_offer(name="first-offer", price=0.40)
    second = _catalog_offer(name="second-offer", price=0.30)

    with patch(
        "dstack._internal.core.backends.vastai.compute.get_catalog_offers",
        side_effect=[[first], [second], []],
    ) as get_catalog_offers:
        compute = VastAICompute(_config(preferred_machine_ids=[901, 902, 901]))
        offers = compute.get_offers_by_requirements(
            _requirements(), full_offers=False, unallocated_resources=False
        )

    assert [offer.instance.name for offer in offers] == ["first-offer", "second-offer"]
    preferred_filters = []
    for call in get_catalog_offers.call_args_list[:2]:
        provider = call.kwargs["catalog"].providers[0]
        preferred_filters.append(provider.extra_filters["machine_id"])
    assert preferred_filters == [{"eq": 901}, {"eq": 902}]
