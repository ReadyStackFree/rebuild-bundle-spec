# ReadyStack Rebuild Bundle

A ReadyStack Rebuild Bundle is a directory (or a tar of one) from which anyone can rebuild a published container image
**offline** and check that the result is **bit-for-bit identical** to the image the publisher distributes. It carries
one signed manifest, `rebuild.json`, that says what "identical" means for that image: the linux/amd64 image-manifest
digest the rebuild must produce, the pinned build engine, the build inputs and a hash over every file in the bundle.

This repository holds:

| Path | What it is |
|---|---|
| [SPEC.md](SPEC.md) | The specification, **v0.1, draft** |
| [rebuild.schema.json](rebuild.schema.json) | JSON Schema for `rebuild.json` |
| [tools/rebuild_bundle.py](tools/rebuild_bundle.py) | The reference verifier: Python 3 standard library only, plus Docker with buildx, and cosign to check the signature |
| [attestations/](attestations/) | How ReadyStack's signed attestations on its images (SLSA v1 provenance, SPDX SBOM, and a Verification Summary Attestation of its release gate) are written, and the policy the VSA names |
| [conformance/](conformance/) | A sample bundle and a GitHub Actions workflow that verifies it on a GitHub-hosted runner |

Licence: Apache-2.0 ([LICENSE](LICENSE)).

## Use

```
tar -xpf bundle.tar.gz                       # keep file modes: they are build input
python3 tools/rebuild_bundle.py validate <dir> --key publisher.pub --signature <bundle>.rebuild.json.sigstore
python3 tools/rebuild_bundle.py verify   <dir> --key publisher.pub --signature <bundle>.rebuild.json.sigstore
```

`validate` builds nothing: it checks the signature over `rebuild.json`, `CHECKSUMS.sha256`, the tree hash, each base
image layout's digest and the builder archive. `verify` then rebuilds with the builder container's network egress
blocked (it needs passwordless `sudo` for iptables) and proven blocked from inside it, and exits 0 only when the
rebuilt linux/amd64 image-manifest digest equals the one in `rebuild.json`. Exit codes: 0 verified; 1 invalid bundle;
2 rebuilt digest differs; 3 isolation not proven; 4 the build did not complete.

## Who uses it

ReadyStack (<https://readystack.dev>) publishes Community Edition container images on Docker Hub as `readystack/*` and
sells the matching build archives. Each reproducible ReadyStack archive is a Rebuild Bundle: it has carried
`rebuild.json` since 2026-10-07, archives published from 2026-10-20 also carry a copy of this verifier
(`docs/rebuild-bundle/rebuild_bundle.py`), and ReadyStack signs `rebuild.json` with its release key
(<https://readystack.dev/keys/cosign.pub>) and publishes the signature beside the archive. This repository
is where the specification and the verifier are published so that anyone can check those claims without trusting
ReadyStack's own tooling, and so that other publishers can produce bundles of their own.

## What it is not

- **Not a build tool.** The bundle names and pins its build engine (BuildKit in v0.1, shipped inside the bundle as an
  image archive); the verifier only runs it.
- **Not a security scan, an SBOM or a licence audit.** A bundle proves which inputs produce an image, not that the
  inputs are free of vulnerabilities.
- **Not equivalence by normalisation.** The rebuilt digest matches or it does not; there is no "close enough".
- **Not multi-architecture** in v0.1: the claim is about the linux/amd64 image manifest only.
- **Not a hosted service.** Everything runs on your machine.
- **Not a SLSA level.** A verified bundle is strong evidence about how an image can be rebuilt; it does not certify the
  platform that originally built it.
- **Not final.** v0.1 is a draft and may change incompatibly before v1.0. The reference verifier writes its result as a
  plain JSON file (`--result`); it does not sign one.

## Status and feedback

Draft v0.1 (2026-10-08). Two conformance bundles were verified on a GitHub-hosted runner on 2026-10-07: the sample in
[conformance/](conformance/) and a production ReadyStack image (caddy v2.11.4-CE-ubuntu24.04-r2, rebuilt to its
published digest `sha256:0772d95e…`; that bundle is a paid ReadyStack archive and is not published here). Issues and
pull requests are welcome.
