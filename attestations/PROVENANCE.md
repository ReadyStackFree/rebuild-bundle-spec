# ReadyStack SLSA v1 provenance (build type v1)

This document is the `buildDefinition.buildType` of the SLSA v1 provenance ReadyStack attaches to its images:
`https://github.com/ReadyStackFree/rebuild-bundle-spec/blob/v0.1.0/attestations/PROVENANCE.md`. It says what each field
means. The provenance follows <https://slsa.dev/spec/v1.0/provenance>.

## Who says it

The provenance is **ReadyStack's assertion from recorded evidence**. ReadyStack's release engine writes it after the
release gate, from the records in the release archive (the ReadyStack Rebuild Bundle that rebuilds the image), and
signs it with the ReadyStack release key. It is not generated or signed by the build platform during the build.
`buildDefinition.internalParameters.readystack.statement` says this in every statement.

Every fact in it is copied from a record in the archive or computed from the archive's own bytes. A fact the archive
does not record is left out rather than filled in. If no record in the archive names the published image (a
`rebuild.json` or `TARGET_DIGEST.txt` naming its linux/amd64 digest, or a locked-digest record naming its index digest),
no provenance is written.

## Builder

`runDetails.builder.id` is `https://readystack.dev/builders/kitforge/v1`: ReadyStack's release engine, which builds
each image from a vendored build context with a pinned BuildKit, with no network access during the build.
`runDetails.builder.version.buildkit` is the BuildKit release the archive pins.

## Fields

### `buildDefinition.externalParameters`

| Field | Meaning |
|---|---|
| `release` | the release tag, e.g. `docker.io/readystack/caddy:v2.11.7-CE-ubuntu24.04-r2` |
| `platform` | `linux/amd64` |
| `source` | the upstream repository, the ref built and its commit, from the archive's source-tree record |
| `build` | from `rebuild.json` (or the archive's own files when an archive predates it): `context`, `dockerfile`, `source_date_epoch`, `mtime_policy`, `schema` |

### `buildDefinition.internalParameters.readystack`

| Field | Meaning |
|---|---|
| `statement` | the sentence in "Who says it" above |
| `authored_by`, `authored_at` | the program that wrote the statement and when |
| `archive` | the release archive's file name, sha256 and size |
| `bound_to_published_image_by` | the archive record that names the published image: `rebuild.json`, `TARGET_DIGEST.txt` or `docs/provenance/LOCKED_DIGEST.txt` |
| `records` | the sha256 of every archive record a fact was read from |
| `rebuild_before_publish` | present only when the archive records that ReadyStack rebuilt the image from it before publishing and got the published digest |

### `buildDefinition.resolvedDependencies`

Each entry is a [ResourceDescriptor](https://github.com/in-toto/attestation/blob/main/spec/v1/resource_descriptor.md).

| Entry | Digest |
|---|---|
| `upstream source` (`uri` `git+<repository>@<ref>`) | `gitCommit` |
| each base image (`name` is the `FROM` reference) | sha256 of the linux/amd64 image manifest in the archive's OCI layout under `bases/` |
| the build engine (`moby/buildkit:<version>`) | sha256 of its image config (its image ID once loaded from `toolchain/buildkit.tar`) |
| `build context` | the Rebuild Bundle tree hash ([SPEC.md](../SPEC.md) section 4) over every file in the archive except `rebuild.json`, `CHECKSUMS.sha256` and `MANIFEST.txt` |
| the Dockerfile | sha256 of the file |
| each top-level file, and each file directly inside a top-level input directory (`vendored/` and the like) | sha256 of the file |
| each directory one level inside a top-level input directory (e.g. `vendored/<name>/`) | the tree hash of that directory, computed as in SPEC.md section 4 with paths relative to it |
| each vendored package archive (`.deb`, wheel, tarball, jar, crate and the like), up to 2,000 | sha256 of the file; above 2,000 they are covered by their directory's tree hash only, and an entry says so |
| the release archive itself | sha256 of the archive file |

`docs/`, `bases/` and `toolchain/` are not listed file by file: `docs/` is not a build input, and the base layouts and
the build engine are listed by digest above. All of them are inside the `build context` tree hash.

### `runDetails.byproducts`

The linux/amd64 image manifest, the image index the release tag names, the image config, and `rebuild.json` when the
archive carries one. The statement's `subject` (written by cosign) is the digest the attestation is attached to: the
index digest or the linux/amd64 manifest digest.

## Check a statement against an archive

```
python3 tools/rebuild_bundle.py tree-hash <extracted archive>             # == the build context digest
python3 tools/rebuild_bundle.py tree-hash <extracted archive>/vendored/x  # == the vendored/x/ entry
sha256sum ReadyStack-<product>-<tag>.tar.gz                              # == the release archive entry
```
