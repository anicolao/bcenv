# NixOS cloud images

bcenv provides two x86-64 NixOS GCE image definitions from a locked Nixpkgs input:

| Image | Included tooling | Intended process |
| --- | --- | --- |
| `competitor-gce` | Nix, Git, Python, Node.js, Java 21, ZIP tools | An inner Battlecode development agent |
| `supervisor-gce` | Nix, Git, Python, Node.js, Google Cloud CLI, SSH | An outer campaign supervisor |

Both use the same base module, a dedicated unprivileged `bcenv` account, key-only SSH, an enabled firewall, and a readiness check. No credentials, campaign data, model weights, or LLM subscription are embedded. These are image and service foundations: no real LLM harness, autonomous supervisor, or official-engine evaluator is configured yet.

## Build and inspect

Use a Linux x86-64 builder with Nix and KVM. A macOS development shell supports editing and evaluation, but cannot execute these Linux VM checks by itself.

```sh
nix develop
npm ci
nix flake check --no-build --all-systems
nix build -L .#competitor-gce --out-link result-competitor
nix build -L .#supervisor-gce --out-link result-supervisor
```

Each output contains a `*.raw.tar.gz` archive with `disk.raw`, using NixOS's [Google Compute image module](https://github.com/NixOS/nixpkgs/blob/21a67dc470149f337cecafbe965d8d252a390518/nixos/modules/virtualisation/google-compute-image.nix). The image has a 16 GiB logical disk; archive and extracted sparse-file sizes differ. Record the archive SHA-256, Nix store path, flake lock, and source commit when publishing an image.

There is intentionally no generic in-image `nixos-rebuild` configuration that discards bcenv's modules. Build a new image from this flake to change a release. Campaign data currently lives under `/var/lib/bcenv` on the boot disk: it survives reboots, **not instance/disk deletion**. Mount a campaign data disk there or export it before replacement. External artifact storage and automatic replacement recovery remain future work.

## Prepare a competitor checkout

Inside a competitor VM, as `bcenv`:

```sh
bcenv-workspace \
  --repository https://github.com/battlecode/battlecode26-scaffold.git \
  --revision f69e2ab872a0061c9d4a684aa1dd798a0829da85
cd /var/lib/bcenv/workspace
java -version
```

The tool fetches the exact commit into a temporary directory and publishes the completed checkout atomically. It refuses mutable branch names, mismatched sources, and unmanaged destinations. Repeating setup preserves subsequent agent commits and uncommitted files. Use a new destination for a different source revision.

This pins the scaffold source, not every dependency its Gradle wrapper may download. Official-engine dependency locking and a real game smoke test are separate season-integration work. The automated image tests use a local Git fixture and do not download or run bot code from GitHub.

## Configure an agent service

The reusable image has no agent enabled. A deployment-specific image can extend the role module with a packaged executable:

```nix
{
  imports = [ bcenv.nixosModules.competitor ];
  services.bcenv.agentCommand = [ "${myAgentPackage}/bin/my-agent" ];
}
```

`myAgentPackage` and `bcenv` are bindings supplied by the consuming flake, not packages included here. The same option exists on the supervisor role. The service starts after the image readiness check, runs as `bcenv`, restarts on failure, and can write its home while the system configuration is read-only. The agent must arrange its own model connection; add deployment-specific runtime credentials using systemd credentials or an equivalent mechanism. Do not put secrets into a Nix expression or environment file in the store.

The fixture agent in VM tests exercises this service contract without a model or cloud account. It is not an implementation of the development loop. OS Login administrators are distinct from the unprivileged agent account. Competitor instances should not receive cloud provisioning authority or another competitor's disks.

## Validation on changes

The `NixOS images` GitHub Actions workflow runs on PRs and main, and can be dispatched manually. It uses the committed flake lock and pinned action revisions. It deliberately does not require cloud or model credentials.

| Check | What it establishes | What it does not establish |
| --- | --- | --- |
| Flake evaluation | Both NixOS configurations, disk derivations, development shells, and tests evaluate | That an image builds or boots |
| Six workspace tests | Exact revision setup, preservation of later work, rejection of unsafe replacements, cleanup after a failed fetch | Official Battlecode behavior |
| Three-node NixOS VM test | Supervisor and two competitors boot; tools work; fixture agents start/restart; workspace edits survive a VM reboot; no agent root access or Docker socket; separate VM filesystems | Cross-VM policy enforcement beyond the tested boundaries, or a real LLM loop |
| Both GCE image builds | The actual deployment archives are produced | Cloud-provider compatibility by itself |
| Actual-image QEMU boot | Each generated `disk.raw` boots and emits its role's readiness marker | GCE metadata, OS Login, IAM, or provider networking |
| Manual GCE smoke | Image import, real provider boot and OS Login, in-guest readiness | Competitive strength |

Run the automated checks on a Linux/KVM host:

```sh
nix flake check -L
nix fmt -- --check flake.nix nix/
nix run .#image-test -- ./result-competitor competitor
nix run .#image-test -- ./result-supervisor supervisor
```

The last two checks require the corresponding image build first. The image test extracts the disk into a temporary directory, boots a QEMU snapshot, waits up to six minutes, and always terminates QEMU and removes its temporary disk. It leaves `boot-<role>.log` for diagnosis. CI uploads boot logs and successful image identities; large image archives are not published automatically.

Use the role VM test for fast changes to services and account boundaries; keep actual-image boot checks because image packaging and boot-loader changes can pass role tests and still break deployment. Add targeted failure cases when a new service or lifecycle operation is implemented. Use a scripted agent for deterministic infrastructure assertions and separately budget real-agent campaigns.

## Import and smoke-test on GCE

Use an existing authorized project, private staging bucket, network, zone, and administrator account with the necessary image/instance and OS Login permissions. These commands create billable resources; they are a deployment recipe, not actions performed by CI. Follow Google's [custom-image documentation](https://cloud.google.com/compute/docs/images/create-custom) for provider requirements.

Set task-specific values, then import one built image:

```sh
export BCENV_PROJECT=your-project
export BCENV_ZONE=your-zone
export BCENV_BUCKET=your-private-staging-bucket
export BCENV_IMAGE=bcenv-competitor-your-release
export BCENV_INSTANCE=bcenv-image-smoke
export BCENV_NETWORK=your-network

gcloud storage cp result-competitor/*.raw.tar.gz \
  "gs://$BCENV_BUCKET/$BCENV_IMAGE.raw.tar.gz"
gcloud compute images create "$BCENV_IMAGE" --project="$BCENV_PROJECT" \
  --source-uri="gs://$BCENV_BUCKET/$BCENV_IMAGE.raw.tar.gz"
gcloud compute instances create "$BCENV_INSTANCE" --project="$BCENV_PROJECT" \
  --zone="$BCENV_ZONE" --image="$BCENV_IMAGE" --image-project="$BCENV_PROJECT" \
  --machine-type=e2-standard-2 --network="$BCENV_NETWORK" --no-address \
  --no-service-account --no-scopes --metadata=enable-oslogin=TRUE
```

This smoke instance has no cloud service account. Configure authorized IAP SSH access to the selected network before connecting; the recipe does not create a public SSH firewall rule.

```sh
gcloud compute ssh "$BCENV_INSTANCE" --project="$BCENV_PROJECT" \
  --zone="$BCENV_ZONE" --tunnel-through-iap \
  --command='sudo -u bcenv bcenv-doctor && systemctl is-active bcenv-image-check'
gcloud compute instances get-serial-port-output "$BCENV_INSTANCE" \
  --project="$BCENV_PROJECT" --zone="$BCENV_ZONE"
```

Record image identity, instance configuration, serial log, and the readiness output. Repeat for the supervisor image. Delete the smoke instance after collecting evidence; delete only images and staging objects created for this smoke test when they are no longer needed. Production supervisor IAM, persistent campaign disks, deployment automation, and real-agent startup require a campaign-specific configuration.
