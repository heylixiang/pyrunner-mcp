from __future__ import annotations

from lib.sandbox_api import SandboxAPI


def test_registry_collects_functions_and_generates_stub():
    api = SandboxAPI()

    @api.function
    def greet(name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    @api.function(name="add_numbers")
    def add(a: int, b: int = 0) -> int:
        """Add two numbers together."""
        return a + b

    assert set(api.callables.keys()) == {"greet", "add_numbers"}
    assert api.callables["greet"]("world") == "Hello, world!"
    assert api.callables["add_numbers"](3, 4) == 7

    stub = api.stub()

    # greet stub
    assert "def greet(name: str) -> str:" in stub
    assert "Say hello." in stub

    # add_numbers stub
    assert "def add_numbers(a: int, b: int = 0) -> int:" in stub
    assert "Add two numbers together." in stub


def test_stub_shows_ellipsis_for_undocumented():
    api = SandboxAPI()

    @api.function
    def noop() -> None:
        pass

    stub = api.stub()
    assert "def noop() -> None:" in stub
    assert "..." in stub
