# ReadyStack release gate policy v1 (for SLSA Verification Summary Attestations)

This is the `policy.uri` of the Verification Summary Attestations (VSAs) ReadyStack attaches to its images:
`https://github.com/ReadyStackFree/rebuild-bundle-spec/blob/v0.1.0/attestations/RELEASE_GATE_POLICY.md`. Its sha256 is
in the VSA's `policy.digest`. The VSA format is <https://slsa.dev/spec/v1.0/verification_summary>.

## Verifier

`verifier.id` is `https://readystack.dev/verifiers/release-gate-g8/v1`: ReadyStack's release gate, which runs once per
release, against the image digest being published and the archive being sold beside it, before either is published.
A release that fails any check below is not published, so every VSA ReadyStack signs says `PASSED`.

## What the gate checks

1. **Archive coverage.** Every document and record the release promises is in the archive, and the archive's
   `CHECKSUMS.sha256` lists it.
2. **Customer scripts.** Every copy of the rebuild and verify scripts in the archive is the current one.
3. **Vulnerability exceptions.** Every vulnerability exception the release states ships in the archive as an OpenVEX
   document, exactly as declared.
4. **Rebuild manifest.** For a reproducible archive, `rebuild.json` describes this archive: the specification's schema,
   the same linux/amd64 digest as the archive's target record, the tree hash recomputed from the archive itself, and no
   forbidden file (a previous rebuild's output, container scaffolding).
5. **Release check.** No fixable critical or high vulnerability (Docker Scout, honouring the release's stated
   exceptions, cross-checked against OSV), the image runs as a non-root user, and SBOM and SLSA provenance
   attestations are attached inside the image index. A release may instead pass with a short list of named residual
   vulnerabilities its build strategy explicitly accepts; such a release is never labelled Grade A.
6. **Scan grade.** The image grades A on ReadyStack's own scan (a syft SBOM and grype matches scored with ce.rodeo's
   published rubric `cerodeo-v1`), measured at publish on the digest being published.
7. **Base OS.** The release tag's base-OS segment names the operating system the scan found in the image.
8. **Offline rebuild.** The archive being sold is rebuilt with the network disabled (`--network=none`). For a
   reproducible archive the rebuilt linux/amd64 image digest must equal the published one, the customer's own
   `VERIFY_OFFLINE.sh` must pass, and the rebuilt image must boot and pass its health check (or the archive records why
   it cannot boot on its own). A passed rebuild of a byte-identical kit within the previous 14 days may stand in for a
   new one; only the documents differ in that case.
9. **Signature and attestations.** The release digest carries a cosign signature that verifies with the published
   release key (fingerprint `04daa92344a52dbc`) and its transparency-log entry, and the image index carries an SPDX
   SBOM and SLSA v1 provenance.

## `verifiedLevels`

ReadyStack's own values; none starts with `SLSA_`, so none claims a SLSA track level.

| Value | Meaning |
|---|---|
| `READYSTACK_RELEASE_GATE_V1` | every check above passed for this release |
| `READYSTACK_OFFLINE_REBUILD_BIT_FOR_BIT_V1` | check 8 compared digests and the rebuilt linux/amd64 image digest equalled the published one |
| `READYSTACK_SCAN_GRADE_A_V1` | check 6 graded the image A at `timeVerified` |

## Other fields

- `resourceUri`: the attested image by digest, `docker.io/readystack/<product>@sha256:<digest>`; the statement's
  subject is the same digest.
- `timeVerified`: when the gate passed. A scan result decays as vulnerabilities are disclosed; ReadyStack re-measures
  every live release daily and publishes the result on its website, separately from this attestation.
- `inputAttestations`: the archive's signed `rebuild.json` (when it has one) and the release archive, by sha256.

## What a VSA is not

It records what ReadyStack's own gate checked, at one time. It is not a third-party audit, and it does not say the
image is free of vulnerabilities.
