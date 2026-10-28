import json
from types import SimpleNamespace

import pytest

from cloudrig import state
from cloudrig.cli import action, dashboard, dispatch, parser, rent
from cloudrig.demo import Demo
from cloudrig.models import CrigError, Rental


def args(*words):
    return parser().parse_intermixed_args(words)


def test_demo_market_and_instances_do_not_contact_accounts(monkeypatch, capsys):
    monkeypatch.setattr("cloudrig.cli.BrokerProvider", lambda _: pytest.fail("Contacted account"))
    dispatch(args("--demo", "--gpu", "--json"))
    assert len(json.loads(capsys.readouterr().out)) == 8
    dispatch(args("--demo", "--rented", "--json"))
    assert len(json.loads(capsys.readouterr().out)) == 3
    assert not state.config_dir().exists()


@pytest.mark.parametrize("command", ["start", "ssh", "exec", "agent", "push", "pull", "approve"])
def test_deferred_commands_fail_before_contacting_account(monkeypatch, command):
    api = Demo()
    monkeypatch.setattr(api, "instances", lambda: pytest.fail("Loaded account for unsupported command"))
    monkeypatch.setattr("cloudrig.cli.Demo", lambda: api)
    with pytest.raises(CrigError, match="deferred"):
        dispatch(args("--demo", "training", command))


def test_rental_only_allocates_and_remembers_the_instance(monkeypatch):
    api = Demo()
    calls = []
    api.rent = lambda rental: calls.append(rental) or "created-1"
    monkeypatch.setattr(
        "cloudrig.cli.connect_editor", lambda *a, **k: pytest.fail("Started editor automatically")
    )
    rental = Rental(api.offers("5090")[0], "training", 40, api.default_image)
    rent(api, rental, args())
    assert calls == [rental]
    assert state.load()["active"]["vastai"] == "created-1"


def test_failed_rental_returns_to_dashboard_without_repeating(monkeypatch):
    api = Demo()
    rental = Rental(api.offers("5090")[0], "training", 40, api.default_image)
    results = iter([rental, None])
    views = []
    calls = []

    def fail(value):
        calls.append(value)
        raise CrigError("Provider rejected the request")

    api.rent = fail

    def view(*a, **kwargs):
        views.append(kwargs)
        return SimpleNamespace(run=lambda: next(results))

    monkeypatch.setattr("cloudrig.cli.CloudrigApp", view)
    dashboard(api, args())
    assert calls == [rental]
    assert views[1]["action_error"] == "Provider rejected the request"


def test_editor_action_connects_without_renting(monkeypatch):
    api = Demo()
    calls = []
    monkeypatch.setattr(
        "cloudrig.cli.connect_editor", lambda provider, identifier, **k: calls.append(identifier)
    )
    action(api, api.instances()[0], "code", args())
    assert calls == [api.instances()[0].id]


@pytest.mark.parametrize("confirmed", [False, True])
def test_delete_requires_confirmation_before_provider_mutation(monkeypatch, confirmed):
    api = Demo()
    calls = []
    api.destroy = calls.append

    def confirm(_):
        if not confirmed:
            raise CrigError("Cancelled")

    monkeypatch.setattr("cloudrig.cli.prompt_confirm", confirm)
    if confirmed:
        action(api, api.instances()[0], "destroy", args())
    else:
        with pytest.raises(CrigError, match="Cancelled"):
            action(api, api.instances()[0], "destroy", args())
    assert calls == ([api.instances()[0].id] if confirmed else [])
