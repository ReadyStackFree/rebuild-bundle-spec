# ReadyStack Rebuild Bundle — specification v0.1 (DRAFT)

> **Status: draft v0.1, 2026-10-08.** Licence: Apache-2.0. Comments and issues welcome.

## 1. What this is

A directory (or a tar of one) from which anyone can rebuild a published container image **offline** and check that the
result is **bit-for-bit identical** to the image a publisher distributes, plus the one signed record that says what
"identical" means for that image. It specifies a layout and a manifest, not a build tool: the build engine is named
and pinned inside the bundle, and a verifier only runs it.

It exists because "reproducible" is otherwise a word on a web page. A bundle makes the claim checkable by a stranger
with Docker and no network, and makes it falsifiable: a mismatch is a recorded failure, not an absence of evidence.

Out of scope for v0.1: multi-arch (linux/amd64 only), equivalence by normalisation (the digest matches or it does
not), vulnerability scanning, SBOM generation, signing policy languages, a hosted service.

## 2. Terms

- **X** — the linux/amd64 image-manifest digest of the published image. The claim is always about X, never about a
  tag or an index digest (Y): attestations and signatures change Y without changing the image.
- **Publisher** — whoever distributes the image and signs the bundle.
- **Verifier** — any program that performs §6.

## 3. Layout

```
<bundle>/
  rebuild.json              required — the manifest (§4)
  CHECKSUMS.sha256          required — sha256 of every file except itself (sha256sum format, paths relative)
  <context>/                required — the build context named by inputs.context (Dockerfile, sources, vendored deps)
  bases/<name>.oci/         required — one OCI image layout per base image, exactly as published (blob identity kept)
  toolchain/builder.tar     required — the build engine image (docker save / OCI archive), loaded, never pulled
  docs/…                    optional — anything else (handbooks, VEX documents, licences)
```

Beside the bundle (not inside it, so the publisher can sign after its release gate without changing the archive):
`<bundle>.rebuild.json` (a copy of the manifest) and `<bundle>.rebuild.json.sigstore`, the detached signature over it
(§5). A verifier takes the signature with `--signature`, or finds `rebuild.json.sigstore` in the bundle root.

Extract with file modes kept (`tar -xpf`): modes are build input and are part of the tree hash.

Forbidden anywhere in the bundle (a verifier refuses it): the outputs of a previous rebuild (any file that records a
rebuilt digest, or a rebuilt image archive), and container-export scaffolding (`.dockerenv`, `/proc`, `/sys` mount
points). Reason: a shipped rebuilt digest lets a verifier "pass" without rebuilding.

## 4. `rebuild.json`

JSON, validated by `rebuild.schema.json`. Fields (all required unless marked):

| Field | Meaning |
|---|---|
| `schema` | `"readystack-rebuild-bundle/v0.1"` |
| `subject.image_manifest_digest` | X |
| `subject.config_digest` | the config digest of X (a cheap early check) |
| `subject.name` *(optional)* | e.g. `docker.io/readystack/caddy:v2.11.4-CE-ubuntu24.04-r2`, for `--online` tag binding |
| `engine.kind` | `"buildkit"` (v0.1). `"melange+apko"` is reserved for v0.2 |
| `engine.image_digest` | the builder image's config digest (its image ID once loaded): `engine.archive` must load to exactly this image |
| `engine.archive` | path of the builder archive in the bundle (`docker save` / OCI image archive holding one image) |
| `engine.tag` *(optional)* | the builder's upstream name, informational (e.g. `moby/buildkit:v0.31.2`); identity is `image_digest` |
| `inputs.context` | context root, relative to the bundle |
| `inputs.dockerfile` | Dockerfile path inside the context |
| `inputs.source_date_epoch` | integer; passed as `SOURCE_DATE_EPOCH` and used to rewrite timestamps |
| `inputs.tree_hash` | sha256 over every file in the bundle except `rebuild.json`, `rebuild.json.sigstore` and `CHECKSUMS.sha256`: for each regular file and symlink in byte-sorted path order, `path NUL octal-mode NUL sha256(content)` (a symlink hashes its target), one record per line; mtimes and directories excluded |
| `inputs.mtime_policy` | `"clamp"`: before building, every context mtime later than `source_date_epoch` is set to it; earlier mtimes are kept. A verifier applies this itself, so the bundle's tar recipe cannot change the result |
| `bases[]` | `{ref, layout, digest}`: each `FROM` reference, the OCI layout that satisfies it, and the digest that layout must hold; empty for a `FROM scratch` image |
| `build_args` *(optional)* | name → value, all non-secret |
| `probe` *(optional)* | how to boot the rebuilt image to show it runs: `{port, http_path}` or `{exec}`, `timeout_s` (≥ 180 recommended), `env`, `secrets` (names; generated at verify time), or `{skip: "<reason>"}` when it needs services the bundle does not contain |

## 5. Trust root

The publisher signs `rebuild.json` (detached; a Sigstore bundle, key-based or keyless). The published sha256 of the
bundle tar and that signature are the trust root; nothing inside the bundle vouches for itself. `CHECKSUMS.sha256`
binds every other file to `rebuild.json` through `inputs.tree_hash`, `bases[].digest` and `engine.image_digest`.

## 6. Verification

1. **validate** — schema; signature over `rebuild.json`; `CHECKSUMS.sha256`; no forbidden files; `inputs.tree_hash`
   recomputed; every `bases[].layout` holds `bases[].digest`; the builder archive loads to `engine.image_digest`.
2. **isolate** — run the builder with no network, and prove it: an egress self-test from inside the build that must
   fail. A verifier that cannot prove isolation reports "not verified", never "verified".
3. **rebuild** — apply `mtime_policy`; build `inputs.dockerfile` in `inputs.context` with the pinned engine, bases
   from their OCI layouts, `SOURCE_DATE_EPOCH` set and layer timestamps rewritten to it, no cache, linux/amd64.
4. **compare** — the rebuilt image-manifest digest equals `subject.image_manifest_digest`. Nothing else counts.
5. **boot** *(optional)* — per `probe`; a `skip` is reported with its reason, not as a pass.
6. **bind** *(optional, `--online`)* — `subject.name` currently resolves to X on its registry.

Exit status of the reference verifier (`rebuild_bundle.py verify`): 0 verified (bit-for-bit and isolation proven);
1 invalid bundle; 2 rebuilt digest differs; 3 isolation not proven (including a build run with `--isolate none`, which
is never reported as verified); 4 the build did not complete. Isolation on a shared host can be container-only
(`--isolate container`: the builder container's egress is rejected and tested from inside it), so the host keeps its
own connection, as a CI runner must.

The result is a SLSA Verification Summary Attestation whose `policy.uri` names this specification, carrying the
rebuilt digest, the verifier version, the host's kernel and engine versions, and the time. On mismatch, the verifier
may run a layer and file diff (e.g. diffoci) and attach it.
