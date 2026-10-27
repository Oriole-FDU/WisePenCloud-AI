import subprocess
import sys
from textwrap import dedent


def test_logging_exception_keeps_error_but_hides_secret_locals():
    script = dedent(
        """
        from common.logger import error, setup_logging_intercept

        setup_logging_intercept(diagnose=False)

        def fail_request():
            api_key = "local-test-secret-4e9c"
            raise RuntimeError("OpenAlex 429")

        try:
            fail_request()
        except RuntimeError as exc:
            error("OpenAlex request failed", exc=exc)
        """
    )
    output = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=True,
        text=True,
    ).stdout

    assert "OpenAlex request failed" in output
    assert "OpenAlex 429" in output
    assert "local-test-secret-4e9c" not in output
