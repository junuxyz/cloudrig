"""Allocate or resume a workspace, then finish its recorded preparation steps."""

import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace

from rich.text import Text

from cloudrig.connections import (
    alias,
    check_rsync,
    open_code,
    require,
    run,
    ssh_command,
    sync_command,
    wait_ready,
)
from cloudrig.models import CrigError, Rental, RentalNotSubmitted, instance_id
from cloudrig.project import Binding, Phase, Project, forget_receipt, save_receipt
from cloudrig.security import child_environment
from cloudrig.terminal import console, waiting


@dataclass(frozen=True)
class Options:
    setup: bool = False
    open_editor: bool = True
    timeout: int = 300
    startup_script: str | None = None
    background: bool = False


def preflight(*, upload: bool, editor: bool) -> None:
    require("ssh")
    if upload:
        check_rsync()
    if editor:
        try:
            result = subprocess.run(
                [require("code"), "--list-extensions"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
                env=child_environment(),
            )
        except subprocess.TimeoutExpired as error:
            raise CrigError("VS Code did not respond. Check its shell command with crig doctor.") from error
        if result.returncode or "ms-vscode-remote.remote-ssh" not in result.stdout.lower().splitlines():
            raise CrigError(
                "Install Remote - SSH first: code --install-extension ms-vscode-remote.remote-ssh"
            )


def launch(project: Project, api, target: Rental | Binding, options: Options) -> Binding:
    if isinstance(target, Rental):
        # Persist uncertainty before making a paid request. Bind its ID before waiting for SSH.
        save_receipt(project, Binding(Phase.SUBMITTING, offer=target.offer.id))
        try:
            identifier = instance_id(api.rent(target))
        except RentalNotSubmitted:
            forget_receipt(project)
            raise
        binding = save_receipt(project, Binding(Phase.PREPARING, id=identifier, offer=target.offer.id))
    else:
        binding = target
        if binding.id is None:
            raise CrigError(
                "The previous rental submission is unresolved. "
                "Inspect crig --rented and the provider console. Recover with crig up --attach ID."
            )
        item = api.instance(binding.id)
        if item.status in {"stopped", "exited"}:
            console.print(Text(f"Resume {item.name} ({item.id}). Fresh sudo will quote the current rate."))
            api.start(item.id)
        elif item.status not in {"running", "loading", "creating", "starting", "initializing"}:
            raise CrigError(
                f"Workspace {item.id} is {item.status}. Inspect crig --rented. No replacement was rented."
            )
    if options.background:
        from cloudrig.preparation import Preparation, start

        start(
            Preparation(
                provider=project.provider,
                identifier=binding.id,
                name=project.name,
                timeout=options.timeout,
                startup_script=options.startup_script,
                code_path=project.remote if options.open_editor else None,
                project=project,
                setup=options.setup,
            )
        )
        console.print(
            f"Preparing {project.name} in the background. Open crig to follow progress.", markup=False
        )
        if options.open_editor:
            console.print("VS Code will open when setup finishes.")
        else:
            console.print("A notification will appear when setup finishes.")
        return binding
    return prepare(project, api, binding, options)


def prepare(
    project: Project,
    api,
    binding: Binding,
    options: Options,
    *,
    progress: Callable[[str], None] | None = None,
) -> Binding:
    console.print(
        Text(f"Workspace instance: {binding.id}. Recovery: crig up {shlex.quote(str(project.root))}")
    )
    with waiting() as update:
        item = wait_ready(
            api,
            binding.id,
            timeout=options.timeout,
            startup_script=options.startup_script,
            progress=progress or update,
        )
    name = alias(item)
    if binding.needs_upload:
        if progress:
            progress("Uploading the project")
        console.print("Uploading the project with Git ignores and Cloudrig's filename exclusions.")
        run(ssh_command(name, f"mkdir -p -- {shlex.quote(project.remote)}"))
        run(sync_command(item, str(project.root) + "/", project.remote + "/", True))
        binding = save_receipt(project, replace(binding, phase=Phase.UPLOADED))
    setup = project.setup
    if options.setup and setup and not binding.setup_complete:
        if progress:
            progress("Installing project dependencies")
        console.print(Text(f"Installing project dependencies: {shlex.join(setup)}"))
        run(ssh_command(name, f"cd {shlex.quote(project.remote)} && {shlex.join(setup)}"))
        python = ".venv/bin/python" if setup[0] == "uv" else "python"
        binding = save_receipt(project, replace(binding, setup_complete=True, python=python))
    console.print("Checking the NVIDIA driver…")
    if progress:
        progress("Checking the GPU")
    command = f"cd {shlex.quote(project.remote)} && nvidia-smi -L"
    if project.framework == "pytorch":
        probe = (
            "import torch; "
            "assert torch.cuda.is_available(), 'PyTorch cannot access CUDA'; "
            "print(torch.cuda.get_device_name(0))"
        )
        command += f" && {shlex.quote(binding.python)} -c {shlex.quote(probe)}"
    run(ssh_command(name, command))
    binding = save_receipt(project, replace(binding, phase=Phase.READY))
    if options.open_editor:
        open_code(item, project.remote)
    console.print(Text(f"Ready: {project.remote}", style="green"))
    console.print(Text(f"Stop compute: crig down {shlex.quote(str(project.root))}"))
    console.print("Remote edits stay on this instance. Pull your outputs before deleting it.")
    return binding
