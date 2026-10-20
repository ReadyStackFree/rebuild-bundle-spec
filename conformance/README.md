# Conformance sample

`hello.bundle.tar.gz` and its detached signature `hello.rebuild.json.sigstore` are attached to the `v0.1.0` release of
this repository. The bundle is signed with the sample key in [`conformance.pub`](conformance.pub), which is not
ReadyStack's release key. The workflow [`.github/workflows/conformance.yml`](../.github/workflows/conformance.yml)
downloads both, extracts the bundle with file modes kept, and runs `tools/rebuild_bundle.py validate` and
`tools/rebuild_bundle.py verify --isolate container` on a GitHub-hosted runner.

| Bundle | What it exercises | Expected linux/amd64 image-manifest digest |
|---|---|---|
| `hello.bundle.tar.gz` | `FROM scratch`, no base image, files with modes and a symlink, a label; the pinned builder is `moby/buildkit:v0.31.2`, shipped inside the bundle | `sha256:a4a60bf1d3b8c6039fe394af13d2a61c7d6a166e44709e686acbdfc507d4b320` |

Run it yourself:

```
gh release download v0.1.0 -R ReadyStackFree/rebuild-bundle-spec -p hello.bundle.tar.gz -p hello.rebuild.json.sigstore
mkdir b && tar -xpzf hello.bundle.tar.gz -C b
python3 tools/rebuild_bundle.py verify b/hello-scratch-v1 --key conformance/conformance.pub \
  --signature hello.rebuild.json.sigstore
```
