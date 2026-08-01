from sandbox.core.config.image import resolve_sandbox_image


LOCAL_IMAGE = (
    "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/"
    "all-in-one-sandbox:latest"
)


def test_local_mode_prefers_environment_image():
    assert resolve_sandbox_image(
        "ghcr.io/agent-infra/sandbox:latest",
        nacos_enabled=False,
        environment_image=LOCAL_IMAGE,
    ) == LOCAL_IMAGE


def test_local_mode_uses_configured_image_when_environment_is_missing():
    configured = "ghcr.io/agent-infra/sandbox:latest"
    assert resolve_sandbox_image(
        configured,
        nacos_enabled=False,
        environment_image=None,
    ) == configured
    assert resolve_sandbox_image(
        configured,
        nacos_enabled=False,
        environment_image="  ",
    ) == configured


def test_nacos_mode_keeps_loaded_configuration_authoritative():
    configured = "nacos.example/sandbox:stable"
    assert resolve_sandbox_image(
        configured,
        nacos_enabled=True,
        environment_image="local.example/sandbox:latest",
    ) == configured
