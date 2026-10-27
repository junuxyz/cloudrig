import json
from dataclasses import replace

import pytest
from textual.widgets import Button, DataTable, Static

from cloudrig import state, workspace, workspace_cli
from cloudrig.demo import Demo
from cloudrig.models import CrigError
from cloudrig.project import Binding, Phase, candidates, load_project, project_lock, receipt, save_receipt
from cloudrig.workspace_tui import WorkspaceApp


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "tiny-model"
    root.mkdir()
    return load_project(str(root), image=Demo.default_image)


def test_manifest_and_explicit_overrides(project):
    (project.root / "crig.toml").write_text('[workspace]\nvram = 32\nmax_hourly = 0.7\nregion = "JP"\n')
    loaded = load_project(str(project.root), vram=80)
    assert loaded.vram == 80 and loaded.region == "JP" and loaded.max_hourly == 0.7
    assert loaded.key == project.key and loaded.remote == project.remote
    assert load_project(str(project.root), provider="runpod").key != project.key


def test_partial_manifest_keeps_global_provider_preference(project):
    (project.root / "crig.toml").write_text("[workspace]\nvram = 32\n")
    loaded = load_project(str(project.root), default_provider="runpod")
    assert loaded.provider == "runpod" and loaded.vram == 32
    assert load_project(str(project.root), default_provider="runpod", provider="vastai").provider == "vastai"


def test_existing_receipts_keep_their_allocation_and_environment(project):
    state.update(
        projects={
            project.key: {
                "phase": "ready",
                "id": "demo-01",
                "python": ".venv/bin/python",
                "setup_complete": True,
                "root": str(project.root),
                "provider": "vastai",
                "remote": project.remote,
            }
        }
    )
    assert receipt(project) == Binding(
        Phase.READY, id="demo-01", python=".venv/bin/python", setup_complete=True
    )


def test_concurrent_up_cannot_submit_another_rental(project, monkeypatch):
    api = Demo()
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: api)
    monkeypatch.setattr(api, "rent", lambda _: pytest.fail("Concurrent command rented a GPU"), raising=False)
    with project_lock(project), pytest.raises(CrigError, match="Another command"):
        workspace_cli.dispatch(["up", str(project.root), "--offer", "84021"])


@pytest.mark.parametrize(
    "content",
    [
        "[workspace]\nvram = nan",
        "[workspace]\ncount = true",
        "[workspace]\ndisk = -1",
        "[workspace]\nhours = 0",
        "[workspace]\nmax_hourly = inf",
        "[workspace]\nunknown = 1",
        '[workspace]\nprovider = "other"',
        "[workspace]\nregion = 1",
    ],
)
def test_invalid_requirements_fail_before_provider_access(project, content):
    (project.root / "crig.toml").write_text(content)
    with pytest.raises(CrigError):
        load_project(str(project.root))


def test_recommendation_uses_per_gpu_memory_exact_count_and_full_quote(project):
    offers = Demo().offers("")
    selected = candidates(replace(project, vram=32), offers)
    assert [row.id for row in selected] == ["84021", "84022", "84023"]
    assert candidates(replace(project, vram=48, max_hourly=3), offers)[0].gpu == "A100 SXM4"
    assert candidates(replace(project, max_hourly=0.28), offers) == []  # $0.28 compute + disk
    assert candidates(project, [replace(offers[0], disk_hourly_gb=None)]) == []
    assert candidates(replace(project, region="jp"), offers)[0].id == "84023"


def test_plan_and_demo_never_connect_or_write_config(project, monkeypatch, capsys):
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: pytest.fail("Demo contacted broker"))
    for command in ("plan", "up"):
        workspace_cli.dispatch([command, str(project.root), "--demo", "--json"])
        result = json.loads(capsys.readouterr().out)
        assert len(result["offers"]) == 4
        assert result["binding"] is None
    workspace_cli.dispatch(["down", str(project.root), "--demo"])
    assert not state.config_dir().exists()


def test_init_respects_demo_and_existing_manifest(project):
    workspace_cli.dispatch(["init", str(project.root), "--demo"])
    assert not (project.root / "crig.toml").exists()
    assert not state.config_dir().exists()
    workspace_cli.dispatch(["init", str(project.root)])
    original = (project.root / "crig.toml").read_text()
    with pytest.raises(CrigError, match="already exists"):
        workspace_cli.dispatch(["init", str(project.root)])
    assert (project.root / "crig.toml").read_text() == original


def test_allocation_receipt_precedes_ssh_and_retry_never_rents_twice(project, monkeypatch):
    calls = []
    api = Demo()
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: api)
    monkeypatch.setattr(workspace_cli, "preflight", lambda **kwargs: None)
    monkeypatch.setattr("cloudrig.workspace_cli.prompt_confirm", lambda _: None)

    def rent(_):
        assert receipt(project).phase == "submitting"
        calls.append("rent")
        return "demo-01"

    def prepare(*args, **kwargs):
        assert receipt(project).id == "demo-01"
        raise CrigError("SSH timed out")

    monkeypatch.setattr(api, "rent", rent, raising=False)
    monkeypatch.setattr(workspace, "prepare", prepare)
    for _ in range(2):
        with pytest.raises(CrigError, match="SSH timed out"):
            workspace_cli.dispatch(["up", str(project.root), "--offer", "84021", "--no-code"])
    assert calls == ["rent"]


def test_ambiguous_paid_submission_is_never_retried(project, monkeypatch):
    api = Demo()
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: api)
    monkeypatch.setattr(workspace_cli, "preflight", lambda **kwargs: None)
    monkeypatch.setattr("cloudrig.workspace_cli.prompt_confirm", lambda _: None)

    def failed(_):
        raise CrigError("Provider timed out after submission")

    monkeypatch.setattr(api, "rent", failed, raising=False)
    with pytest.raises(CrigError, match="Provider timed out"):
        workspace_cli.dispatch(["up", str(project.root), "--offer", "84021", "--no-code"])
    monkeypatch.setattr(api, "rent", lambda _: pytest.fail("Duplicate paid submission"), raising=False)
    with pytest.raises(CrigError, match="unresolved"):
        workspace_cli.dispatch(["up", str(project.root), "--offer", "84021", "--no-code"])


def test_reconnect_skips_upload_even_after_cuda_probe_failure(project, monkeypatch):
    save_receipt(project, Binding(Phase.PREPARING, id="demo-01"))
    api = Demo()
    commands = []
    monkeypatch.setattr(workspace, "wait_ready", lambda *a, **kw: api.instance("demo-01"))
    monkeypatch.setattr(workspace, "ssh_command", lambda *args: ["ssh", *args])
    monkeypatch.setattr(workspace, "sync_command", lambda *a: ["rsync", "project/"])

    def run(argv):
        commands.append(argv)
        if "nvidia-smi" in argv[-1]:
            raise CrigError("CUDA unavailable")

    monkeypatch.setattr(workspace, "run", run)
    for _ in range(2):
        with pytest.raises(CrigError, match="CUDA unavailable"):
            workspace.prepare(project, api, receipt(project), workspace.Options(open_editor=False))
    assert receipt(project).phase == "uploaded"
    assert len([cmd for cmd in commands if cmd[0] == "rsync"]) == 1


def test_ready_reconnect_preserves_python_environment_and_remote_files(project, monkeypatch):
    save_receipt(project, Binding(Phase.READY, id="demo-01", python=".venv/bin/python", setup_complete=True))
    api, commands = Demo(), []
    monkeypatch.setattr(workspace, "wait_ready", lambda *a, **kw: api.instance("demo-01"))
    monkeypatch.setattr(workspace, "ssh_command", lambda *args: ["ssh", *args])
    monkeypatch.setattr(workspace, "sync_command", lambda *a: pytest.fail("Reconnect overwrote remote files"))
    monkeypatch.setattr(workspace, "run", commands.append)
    workspace.prepare(project, api, receipt(project), workspace.Options(open_editor=False))
    assert ".venv/bin/python -c" in commands[-1][-1]


def test_down_only_stops_project_allocation_and_keeps_binding(project, monkeypatch):
    save_receipt(project, Binding(Phase.READY, id="demo-01"))
    api, calls = Demo(), []
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: api)
    monkeypatch.setattr(api, "stop", calls.append, raising=False)
    monkeypatch.setattr("cloudrig.workspace_cli.prompt_confirm", lambda _: None)
    workspace_cli.dispatch(["down", str(project.root)])
    assert calls == ["demo-01"] and receipt(project).id == "demo-01"


def test_forget_only_removes_binding_without_contacting_provider(project, monkeypatch):
    save_receipt(project, Binding(Phase.SUBMITTING))
    state.update(provider="vastai")
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: pytest.fail("Forget contacted provider"))
    monkeypatch.setattr("cloudrig.workspace_cli.prompt_confirm", lambda _: None)
    workspace_cli.dispatch(["forget", str(project.root)])
    assert receipt(project) is None and state.load()["provider"] == "vastai"


def test_missing_bound_instance_never_falls_back_to_rental(project, monkeypatch):
    save_receipt(project, Binding(Phase.READY, id="missing"))
    api = Demo()
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: api)
    monkeypatch.setattr(workspace_cli, "preflight", lambda **kwargs: None)
    monkeypatch.setattr(api, "rent", lambda _: pytest.fail("Rented a replacement"), raising=False)
    monkeypatch.setattr(api, "instance", lambda _: (_ for _ in ()).throw(CrigError("Missing instance")))
    with pytest.raises(CrigError, match="Missing instance"):
        workspace_cli.dispatch(["up", str(project.root), "--offer", "84021", "--no-code"])


def test_recover_ambiguous_rental_by_attaching_approved_id(project, monkeypatch):
    save_receipt(project, Binding(Phase.SUBMITTING))
    api, calls = Demo(), []
    item = replace(api.instance("demo-01"), approved=True)
    monkeypatch.setattr(api, "instance", lambda _: item)
    monkeypatch.setattr(api, "rent", lambda _: pytest.fail("Attach rented another GPU"), raising=False)
    monkeypatch.setattr("cloudrig.workspace_cli.prompt_confirm", lambda _: None)
    monkeypatch.setattr(workspace, "prepare", lambda *a: calls.append(a[2].phase))
    monkeypatch.setattr(workspace_cli, "BrokerProvider", lambda _: api)
    monkeypatch.setattr(workspace_cli, "preflight", lambda **kwargs: None)
    workspace_cli.dispatch(["up", str(project.root), "--attach", "demo-01"])
    assert receipt(project).id == "demo-01" and calls == [Phase.PREPARING]


@pytest.mark.parametrize("size", [(120, 38), (80, 24)])
async def test_workspace_review_is_keyboard_usable(project, size):
    rows = candidates(project, Demo().offers(""))
    app = WorkspaceApp(project, rows, demo=True)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 4
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.query_one("#launch", Button).has_focus
        await pilot.press("enter")
    assert app.return_value.id == "84021"


async def test_empty_review_cannot_launch(project):
    app = WorkspaceApp(project, [], demo=True)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.query_one("#launch", Button).disabled
        assert "No complete quotes" in str(app.query_one("#summary", Static).render())
