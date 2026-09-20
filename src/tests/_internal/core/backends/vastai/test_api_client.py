import json

import httpx

from dstack._internal.core.backends.vastai.api_client import VastAIAPIClient, VastAIRateLimitError


def test_attach_ssh_key_posts_to_instance_endpoint():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    client = VastAIAPIClient(api_key="test-api-key")
    client.s.close()
    client.s = httpx.Client(
        base_url="https://console.vast.ai/api/",
        transport=httpx.MockTransport(handler),
    )

    client.attach_ssh_key(51729340, "ssh-ed25519 AAAATEST")

    assert len(requests) == 1
    assert requests[0].url.path == "/api/v0/instances/51729340/ssh/"
    assert json.loads(requests[0].content) == {"ssh_key": "ssh-ed25519 AAAATEST"}


def test_attach_ssh_key_raises_rate_limit_error():
    client = VastAIAPIClient(api_key="test-api-key")
    client.s.close()
    client.s = httpx.Client(
        base_url="https://console.vast.ai/api/",
        transport=httpx.MockTransport(lambda request: httpx.Response(429)),
    )

    try:
        client.attach_ssh_key(51729340, "ssh-ed25519 AAAATEST")
    except VastAIRateLimitError:
        pass
    else:
        raise AssertionError("VastAIRateLimitError was not raised")
