# Release packages

Cloudrig wheels contain the CLI, native helper, installer, and uninstaller. Supported targets are
macOS 13+ and Linux on ARM64 and x86-64. Python must be 3.11 or newer. Linux needs systemd and sudo.

The package checks the bundled helper's platform, version, and file hashes before installation.
It does not download a helper or compile code during first-time setup.

## Verify a downloaded release

Official releases come from [junuxyz/cloudrig](https://github.com/junuxyz/cloudrig/releases).
`crig update` uses GitHub CLI to verify the official workflow signature and version tag before installing.
For the first installation, verify the downloaded wheel with GitHub CLI:

```sh
gh attestation verify ./cloudrig-VERSION-PLATFORM.whl \
  --repo junuxyz/cloudrig \
  --signer-workflow junuxyz/cloudrig/.github/workflows/release.yml \
  --deny-self-hosted-runners
```

Proceed only if verification succeeds for the intended repository and release commit.
A matching file hash alone does not establish who published it. Local builds have no GitHub attestation.

The repository currently has no published release. Hosted builds and end-to-end signed updates
remain unverified until the first release is published. Local builds cannot pass official-release
verification; source contributors should install their own builds directly with uv.

## Build a release

The release workflow builds four platform wheels, generates GitHub build attestations,
verifies their workflow identity, and saves a `verified-release-wheels` artifact with checksums.
It runs on version tags or manual dispatch. A version tag also creates a draft GitHub Release
with the verified wheels. Review the draft and publish it to make that version available to the updater.
Manual workflow runs create artifacts only. Nothing is published to a package index.

Before distributing a version, run the checks, test installed wheels on both operating systems,
and verify the hosted attestations. Publish only the verified artifacts.
See [GitHub's verification documentation](https://cli.github.com/manual/gh_attestation_verify).

For a local build:

```sh
uv build
```

Building from source requires Go 1.26+. `GOOS` and `GOARCH` select a cross-compilation target.
The build produces a platform wheel rather than a Python-only wheel. Do not distribute an older
wheel with a matching version number after changing the helper; increment the package version.
