import json
from dataclasses import replace

import pytest
from textual.widgets import Button, DataTable, Static

from cloudrig import state, workspace
from cloudrig.demo import Demo
from cloudrig.models import CrigError
from cloudrig.project import candidates, load_project, receipt, save_receipt
from cloudrig.workspace_tui import DemoReadyApp, WorkspaceApp


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
    monkeypatch.setattr(workspace, "connect", lambda _: pytest.fail("Demo contacted broker"))
    for command in ("plan", "up"):
        workspace.dispatch([command, str(project.root), "--demo", "--json"])
        result = json.loads(capsys.readouterr().out)
        assert len(result["offers"]) == 4
        assert result["binding"] is None
    workspace.dispatch(["down", str(project.root), "--demo"])
    assert not state.config_dir().exists()


def test_init_never_overwrites_a_manifest(project):
    workspace.dispatch(["init", str(project.root)])
    original = (project.root / "crig.toml").read_text()
    with pytest.raises(CrigError, match="already exists"):
        workspace.dispatch(["init", str(project.root)])
    assert (project.root / "crig.toml").read_text() == original


def test_init_demo_never_writes_manifest(project):
    workspace.dispatch(["init", str(project.root), "--demo"])
    assert not (project.root / "crig.toml").exists()
    assert not state.config_dir().exists()


def launch_args():
    return workspace.parser().parse_args(["up", "--offer", "84021", "--no-code"])


def test_allocation_receipt_precedes_ssh_and_retry_never_rents_twice(project, monkeypatch):
    calls = []
    api = Demo()
    monkeypatch.setattr("cloudrig.cli.prompt_confirm", lambda _: None)

    def rent(_):
        assert receipt(project)["phase"] == "submitting"
        calls.append("rent")
        return "demo-01"

    def prepare(*args, **kwargs):
        assert receipt(project)["id"] == "demo-01"
        raise CrigError("SSH timed out")

    monkeypatch.setattr(api, "rent", rent)
    monkeypatch.setattr(workspace, "prepare", prepare)
    for _ in range(2):
        with pytest.raises(CrigError, match="SSH timed out"):
            workspace.launch(project, api, launch_args())
    assert calls == ["rent"]


def test_ambiguous_paid_submission_is_never_retried(project, monkeypatch):
    api = Demo()
    monkeypatch.setattr("cloudrig.cli.prompt_confirm", lambda _: None)

    def failed(_):
        raise CrigError("Provider timed out after submission")

    monkeypatch.setattr(api, "rent", failed)
    with pytest.raises(CrigError, match="Provider timed out"):
        workspace.launch(project, api, launch_args())
    monkeypatch.setattr(api, "rent", lambda _: pytest.fail("Duplicate paid submission"))
    with pytest.raises(CrigError, match="unresolved"):
        workspace.launch(project, api, launch_args())


def test_reconnect_skips_upload_even_after_cuda_probe_failure(project, monkeypatch):
    save_receipt(project, id="demo-01", phase="preparing")
    api = Demo()
    commands = []
    monkeypatch.setattr(workspace, "wait_ready", lambda *a, **kw: api.instance("demo-01"))
    monkeypatch.setattr(workspace, "install_alias", lambda _: "test-host")
    monkeypatch.setattr(workspace, "ssh_command", lambda *args: ["ssh", *args])
    monkeypatch.setattr(workspace, "sync_command", lambda *a: ["rsync", "project/"])

    def run(argv):
        commands.append(argv)
        if "nvidia-smi" in argv[-1]:
            raise CrigError("CUDA unavailable")

    monkeypatch.setattr(workspace, "run", run)
    for reconnect in (False, True):
        with pytest.raises(CrigError, match="CUDA unavailable"):
            workspace.prepare(project, api, "demo-01", launch_args(), reconnect=reconnect)
    assert receipt(project)["phase"] == "uploaded"
    assert len([cmd for cmd in commands if cmd[0] == "rsync"]) == 1


def test_ready_reconnect_preserves_python_environment_and_remote_files(project, monkeypatch):
    save_receipt(project, id="demo-01", phase="ready", python=".venv/bin/python", setup_complete=True)
    api, commands = Demo(), []
    monkeypatch.setattr(workspace, "wait_ready", lambda *a, **kw: api.instance("demo-01"))
    monkeypatch.setattr(workspace, "install_alias", lambda _: "test-host")
    monkeypatch.setattr(workspace, "ssh_command", lambda *args: ["ssh", *args])
    monkeypatch.setattr(workspace, "sync_command", lambda *a: pytest.fail("Reconnect overwrote remote files"))
    monkeypatch.setattr(workspace, "run", commands.append)
    workspace.prepare(project, api, "demo-01", launch_args(), reconnect=True)
    assert ".venv/bin/python -c" in commands[-1][-1]


def test_down_only_stops_project_allocation_and_keeps_binding(project, monkeypatch):
    save_receipt(project, id="demo-01", phase="ready")
    api, calls = Demo(), []
    monkeypatch.setattr(workspace, "connect", lambda _: api)
    monkeypatch.setattr(api, "stop", calls.append)
    monkeypatch.setattr("cloudrig.cli.prompt_confirm", lambda _: None)
    workspace.dispatch(["down", str(project.root)])
    assert calls == ["demo-01"] and receipt(project)["id"] == "demo-01"


def test_forget_only_removes_binding_without_contacting_provider(project, monkeypatch):
    save_receipt(project, phase="submitting")
    state.update(provider="vastai")
    monkeypatch.setattr(workspace, "connect", lambda _: pytest.fail("Forget contacted provider"))
    monkeypatch.setattr("cloudrig.cli.prompt_confirm", lambda _: None)
    workspace.dispatch(["forget", str(project.root)])
    assert receipt(project) is None and state.load()["provider"] == "vastai"


def test_missing_bound_instance_never_falls_back_to_rental(project, monkeypatch):
    save_receipt(project, id="missing", phase="ready")
    api = Demo()
    monkeypatch.setattr(api, "rent", lambda _: pytest.fail("Rented a replacement"))
    monkeypatch.setattr(api, "instance", lambda _: (_ for _ in ()).throw(CrigError("Missing instance")))
    with pytest.raises(CrigError, match="Missing instance"):
        workspace.launch(project, api, launch_args())


def test_recover_ambiguous_rental_by_attaching_approved_id(project, monkeypatch):
    save_receipt(project, phase="submitting")
    api, calls = Demo(), []
    item = replace(api.instance("demo-01"), approved=True)
    monkeypatch.setattr(api, "instance", lambda _: item)
    monkeypatch.setattr(api, "rent", lambda _: pytest.fail("Attach rented another GPU"))
    monkeypatch.setattr("cloudrig.cli.prompt_confirm", lambda _: None)
    monkeypatch.setattr(workspace, "prepare", lambda *a, **kw: calls.append(kw))
    args = workspace.parser().parse_args(["up", "--attach", "demo-01"])
    workspace.launch(project, api, args)
    assert receipt(project)["id"] == "demo-01" and calls == [{"reconnect": False}]


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


async def test_empty_review_cannot_launch_and_ready_screen_is_explicit_simulation(project):
    app = WorkspaceApp(project, [], demo=True)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.query_one("#launch", Button).disabled
        assert "No complete quotes" in str(app.query_one("#summary", Static).render())
    ready = DemoReadyApp(project, Demo().offers("")[0])
    async with ready.run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        assert any("SIMULATED RESULT" in str(widget.render()) for widget in ready.query(Static))
