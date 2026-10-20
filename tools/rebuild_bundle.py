#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Reference verifier for the ReadyStack Rebuild Bundle specification v0.1 (draft):
# https://github.com/ReadyStackFree/rebuild-bundle-spec
"""rebuild_bundle.py — make, validate and verify a ReadyStack Rebuild Bundle (docs/readystack-rebuild-bundle/SPEC.md).

Standard library only, so the same file runs in the engine (package_archive on the build host, G8), on a customer's
machine and on a GitHub-hosted runner.

  rebuild_bundle.py make <bundle-dir> [--write]            build rebuild.json from the archive's records
  rebuild_bundle.py tree-hash <bundle-dir>
  rebuild_bundle.py validate <bundle-dir> [--key PUB] [--signature FILE]
  rebuild_bundle.py verify <bundle-dir> [--key PUB] [--signature FILE] [--isolate container|host|none]
                                        [--result FILE]

`verify` exits 0 only when the rebuilt linux/amd64 image-manifest digest equals subject.image_manifest_digest AND the
build was proven isolated (or --isolate none was asked for, which is reported as "not isolated", never as verified).
Exit codes: 0 verified; 1 invalid bundle; 2 rebuilt digest differs; 3 isolation not proven; 4 build failed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

SCHEMA = "readystack-rebuild-bundle/v0.1"
# written at or after rebuild.json, so outside the tree it hashes (root level only)
NOT_IN_TREE = ("rebuild.json", "rebuild.json.sigstore", "CHECKSUMS.sha256", "MANIFEST.txt")
# a previous rebuild's outputs (a verifier could "pass" without rebuilding) and container-export scaffolding
FORBIDDEN_FILES = ("REBUILT_DIGEST.txt", "rebuilt.oci.tar", "rebuilt.docker.tar")
FORBIDDEN_ANYWHERE = (".dockerenv",)
PROBE_KEYS = {"probe_port": "port", "probe_path": "http_path", "probe_timeout": "timeout_s", "probe_exec": "exec"}
BUILDER_LOCAL = "readystack.invalid/buildkit:pinned"   # a name no registry serves: buildx can never pull it
EGRESS_TEST_URL = "https://github.com/"


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- tree hash
def _record(rel, mode, sha):
    return f"{rel}\0{mode:o}\0{sha}\n".encode()


def tree_hash(root) -> str:
    """sha256 over every regular file and symlink except NOT_IN_TREE at the root: `path NUL octal-mode NUL sha256`
    per entry, byte-sorted by path. mtimes and directories are excluded; a symlink hashes its target."""
    recs = []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in fns + [d for d in dns if os.path.islink(os.path.join(dp, d))]:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if rel in NOT_IN_TREE:
                continue
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                recs.append((rel.encode(), _record(rel, 0o777, hashlib.sha256(os.readlink(p).encode()).hexdigest())))
            elif stat.S_ISREG(st.st_mode):
                recs.append((rel.encode(), _record(rel, stat.S_IMODE(st.st_mode), _sha_file(p))))
    h = hashlib.sha256()
    for _, r in sorted(recs):
        h.update(r)
    return "sha256:" + h.hexdigest()


def tree_hash_tar(tar_path, top) -> str:
    """The same hash computed straight from a sale tar (G8 reads the archive it publishes, without extracting).

    A HARD LINK member is the same file as its target once extracted, so it hashes as a regular file with the
    target's content (2026-10-10: prometheus v3.15.0 and coder v2.37.3 vendor pnpm node_modules, which dedupes with
    hard links; GNU tar stores every name after the first as a link member, which this function skipped while the
    extracted tree counts each one — 23,408 missing entries and a false 'rebuild.json does not describe this
    archive' at G8)."""
    recs, by_name, links = [], {}, []
    with tarfile.open(tar_path, "r:*") as t:
        for m in t:
            name = m.name.rstrip("/")
            if not name.startswith(top + "/"):
                continue
            rel = name[len(top) + 1:]
            if m.issym():
                if rel not in NOT_IN_TREE:
                    recs.append((rel.encode(), _record(rel, 0o777, hashlib.sha256(m.linkname.encode()).hexdigest())))
            elif m.isfile() and not m.islnk():
                h = hashlib.sha256()
                f = t.extractfile(m)
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
                by_name[name] = h.hexdigest()
                if rel not in NOT_IN_TREE:
                    recs.append((rel.encode(), _record(rel, m.mode & 0o7777, h.hexdigest())))
            elif m.islnk() and rel not in NOT_IN_TREE:
                links.append((rel, m.mode & 0o7777, m.linkname.rstrip("/")))
    for rel, mode, target in links:
        if target not in by_name:
            raise ValueError(f"hard link {rel} points at {target}, which is not a regular file in the archive")
        recs.append((rel.encode(), _record(rel, mode, by_name[target])))
    h = hashlib.sha256()
    for _, r in sorted(recs):
        h.update(r)
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------------------------------- facts
def builder_config_digest(archive) -> tuple[str, str | None]:
    """(config digest, first RepoTag) of the single image in a `docker save` / OCI image archive."""
    with tarfile.open(archive, "r:*") as t:
        man = json.load(t.extractfile("manifest.json"))
        if len(man) != 1:
            raise ValueError(f"{archive}: expected one image, found {len(man)}")
        cfg = man[0]["Config"]
        h = hashlib.sha256()
        f = t.extractfile(cfg)
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
        return "sha256:" + h.hexdigest(), (man[0].get("RepoTags") or [None])[0]


def layout_digest(layout_dir) -> str:
    with open(os.path.join(layout_dir, "index.json")) as f:
        return json.load(f)["manifests"][0]["digest"]


def _kv(path):
    out = []
    if os.path.exists(path):
        for line in open(path).read().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out.append((k.strip(), v.strip()))
    return out


def make(root) -> dict:
    """rebuild.json from the records a ReadyStack archive already carries (TARGET_DIGEST.txt, SOURCE_DATE_EPOCH.txt,
    DOCKERFILE_NAME.txt, BASES.tsv, toolchain/)."""
    j = lambda *p: os.path.join(root, *p)
    td = _kv(j("TARGET_DIGEST.txt"))
    tdd = dict(td)
    if not tdd.get("image_manifest_digest"):
        raise ValueError("TARGET_DIGEST.txt has no image_manifest_digest: not a reproducible archive")
    sde = int("".join(c for c in open(j("SOURCE_DATE_EPOCH.txt")).read() if c.isdigit()))
    dockerfile = (open(j("DOCKERFILE_NAME.txt")).read().strip() if os.path.exists(j("DOCKERFILE_NAME.txt"))
                  else "Dockerfile.offline.pinned")
    archive = "toolchain/buildkit.tar"
    cfg, tag = builder_config_digest(j(archive))
    bases = []
    if os.path.exists(j("BASES.tsv")):
        for line in open(j("BASES.tsv")).read().splitlines():
            if not line.strip():
                continue
            ref, path = line.split("\t", 1)
            bases.append({"ref": ref, "layout": path, "digest": layout_digest(j(path))})
    subject = {"image_manifest_digest": tdd["image_manifest_digest"]}
    if tdd.get("config_digest"):
        subject["config_digest"] = tdd["config_digest"]
    if tdd.get("tag"):
        subject["name"] = "docker.io/" + tdd["tag"] if tdd["tag"].count("/") == 1 else tdd["tag"]
    doc = {"schema": SCHEMA, "subject": subject,
           "engine": {"kind": "buildkit", "image_digest": cfg, "archive": archive,
                      **({"tag": tag} if tag else {})},
           "inputs": {"context": ".", "dockerfile": dockerfile, "source_date_epoch": sde,
                      "tree_hash": tree_hash(root), "mtime_policy": "clamp"},
           "bases": bases}
    probe = {}
    for k, v in td:
        if k in PROBE_KEYS:
            probe[PROBE_KEYS[k]] = int(v) if k in ("probe_port", "probe_timeout") else v
        elif k == "verify_env":
            ek, ev = v.split("=", 1)
            probe.setdefault("env", {})[ek] = ev
        elif k == "verify_secret":
            probe.setdefault("secrets", []).append(v)
        elif k == "verify_boot_reason":
            probe["skip"] = v
    if "skip" in probe:
        probe = {"skip": probe["skip"]}
    elif "exec" in probe:
        probe.pop("http_path", None)
    if probe:
        doc["probe"] = probe
    return doc


# ---------------------------------------------------------------------------------------------- validate
def _check_signature(root, key, signature):
    sig = signature or os.path.join(root, "rebuild.json.sigstore")
    if not os.path.exists(sig):
        return False, f"no signature ({sig})"
    cosign = shutil.which("cosign")
    if not cosign:
        return False, "cosign not installed: the signature cannot be checked"
    r = subprocess.run([cosign, "verify-blob", "--key", key, "--bundle", sig, os.path.join(root, "rebuild.json")],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-300:]


def validate(root, key=None, signature=None) -> list:
    """Every reason this bundle must not be trusted (empty list == valid). Reads, never modifies."""
    errs = []
    j = lambda *p: os.path.join(root, *p)
    try:
        doc = json.load(open(j("rebuild.json")))
    except (OSError, ValueError) as e:
        return [f"rebuild.json unreadable: {e}"]
    if doc.get("schema") != SCHEMA:
        errs.append(f"schema is {doc.get('schema')!r}, expected {SCHEMA}")
    for k in ("subject", "engine", "inputs", "bases"):
        if k not in doc:
            errs.append(f"rebuild.json has no {k}")
    if errs:
        return errs
    # CHECKSUMS.sha256 binds every other file
    if not os.path.exists(j("CHECKSUMS.sha256")):
        errs.append("CHECKSUMS.sha256 missing")
    else:
        bad = []
        for line in open(j("CHECKSUMS.sha256")).read().splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            want, rel = parts[0], parts[1].lstrip("*")
            rel = rel[2:] if rel.startswith("./") else rel
            p = j(rel)
            if not os.path.isfile(p) or _sha_file(p) != want:
                bad.append(rel)
        if bad:
            errs.append(f"CHECKSUMS.sha256: {len(bad)} file(s) differ or are missing, e.g. {bad[:3]}")
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), root)
            if fn in FORBIDDEN_ANYWHERE or rel in FORBIDDEN_FILES:
                errs.append(f"forbidden file in the bundle: {rel}")
    th = tree_hash(root)
    if th != doc["inputs"].get("tree_hash"):
        errs.append(f"tree hash {th} != rebuild.json {doc['inputs'].get('tree_hash')} "
                    "(extract with `tar -xpf` so file modes are kept)")
    for b in doc.get("bases") or []:
        try:
            got = layout_digest(j(b["layout"]))
        except (OSError, ValueError, KeyError) as e:
            errs.append(f"base layout {b.get('layout')} unreadable: {e}"); continue
        if got != b.get("digest"):
            errs.append(f"base layout {b['layout']} holds {got}, rebuild.json says {b.get('digest')}")
    try:
        cfg, _ = builder_config_digest(j(doc["engine"]["archive"]))
        if cfg != doc["engine"].get("image_digest"):
            errs.append(f"builder archive holds {cfg}, rebuild.json says {doc['engine'].get('image_digest')}")
    except (OSError, ValueError, KeyError) as e:
        errs.append(f"builder archive unreadable: {e}")
    if not os.path.isfile(j(doc["inputs"].get("dockerfile") or "")):
        errs.append(f"Dockerfile {doc['inputs'].get('dockerfile')} missing")
    if key:
        ok, why = _check_signature(root, key, signature)
        if not ok:
            errs.append(f"signature over rebuild.json does not verify with {key}: {why}")
    return errs


# ---------------------------------------------------------------------------------------------- verify
def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


_ISOLATE_SH = r'''
set -e
CHAINS="$1"
for CH in $CHAINS; do
  sudo -n iptables -N RS_RRB_$CH 2>/dev/null || sudo -n iptables -F RS_RRB_$CH
  sudo -n iptables -A RS_RRB_$CH -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  [ "$CH" = OUTPUT ] && sudo -n iptables -A RS_RRB_$CH -d 127.0.0.0/8 -j RETURN
  sudo -n iptables -A RS_RRB_$CH -j REJECT
  sudo -n iptables -C $CH -j RS_RRB_$CH 2>/dev/null || sudo -n iptables -I $CH 1 -j RS_RRB_$CH
done
'''
_UNISOLATE_SH = r'''
for CH in OUTPUT DOCKER-USER; do
  while sudo -n iptables -D $CH -j RS_RRB_$CH 2>/dev/null; do :; done
  sudo -n iptables -F RS_RRB_$CH 2>/dev/null; sudo -n iptables -X RS_RRB_$CH 2>/dev/null
done; true
'''


def _clamp_mtimes(root, sde):
    n = 0
    for dp, dns, fns in os.walk(root):
        for x in dns + fns:
            p = os.path.join(dp, x)
            st = os.lstat(p)
            if st.st_mtime > sde:
                os.utime(p, (sde, sde), follow_symlinks=False)
                n += 1
    if os.lstat(root).st_mtime > sde:
        os.utime(root, (sde, sde))
    return n


def verify(root, key=None, signature=None, isolate="container", log=print) -> dict:
    res = {"spec": SCHEMA, "bundle": os.path.abspath(root), "verified": False, "isolation": isolate,
           "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "host": {"kernel": platform.release(), "machine": platform.machine()}}
    errs = validate(root, key, signature)
    res["validation"] = errs
    if errs:
        res["result"], res["exit"] = "invalid bundle", 1
        return res
    doc = json.load(open(os.path.join(root, "rebuild.json")))
    want = doc["subject"]["image_manifest_digest"]
    sde = int(doc["inputs"]["source_date_epoch"])
    res["subject"] = want
    res["host"]["docker"] = _run(["docker", "version", "-f", "{{.Server.Version}}"]).stdout.strip()
    res["host"]["buildx"] = _run(["docker", "buildx", "version"]).stdout.strip()
    builder = f"rrb-{os.getpid()}"
    out_dir = tempfile.mkdtemp(prefix="rrb-out-")
    chains = {"container": "DOCKER-USER", "host": "OUTPUT DOCKER-USER"}.get(isolate)
    try:
        log("==> loading the pinned builder from the bundle")
        ld = _run(["docker", "load", "-i", os.path.join(root, doc["engine"]["archive"])])
        loaded = next((l.split(":", 1)[1].strip() for l in ld.stdout.splitlines() if l.startswith("Loaded image")), None)
        if ld.returncode or not loaded:
            res["result"], res["exit"] = f"builder did not load: {ld.stderr[-200:]}", 4
            return res
        _run(["docker", "tag", loaded, BUILDER_LOCAL])
        if chains:
            log(f"==> blocking network egress ({isolate})")
            r = _run(["bash", "-c", _ISOLATE_SH, "_", chains])
            if r.returncode:
                res["result"], res["exit"] = f"could not block egress: {r.stderr[-200:]}", 3
                return res
        mk = _run(["docker", "buildx", "create", "--name", builder, "--driver", "docker-container",
                   "--driver-opt", f"image={BUILDER_LOCAL}", "--bootstrap"])
        if mk.returncode:
            res["result"], res["exit"] = f"builder did not start: {mk.stderr[-300:]}", 4
            return res
        if chains:
            # the proof, from inside the builder container that will run the build
            ctr = f"buildx_buildkit_{builder}0"
            has = _run(["docker", "exec", ctr, "sh", "-c", "command -v wget"])
            probe = _run(["docker", "exec", ctr, "wget", "-q", "-T", "8", "-O", "/dev/null", EGRESS_TEST_URL])
            res["egress_self_test"] = {"tool": bool(has.stdout.strip()), "reached_internet": probe.returncode == 0}
            if not has.stdout.strip() or probe.returncode == 0:
                res["result"], res["exit"] = "isolation not proven (the builder reached the internet or could not test)", 3
                return res
            log("==> isolation proven: the builder container cannot reach the internet")
        n = _clamp_mtimes(root, sde)
        log(f"==> mtimes clamped to SOURCE_DATE_EPOCH={sde} ({n} entries)")
        ctx = []
        for b in doc["bases"]:
            ctx += ["--build-context", f"{b['ref']}=oci-layout://{os.path.abspath(os.path.join(root, b['layout']))}@{b['digest']}"]
        log("==> building (no network, no cache, linux/amd64)")
        t0 = time.time()
        bd = subprocess.run(["docker", "buildx", "build", "--builder", builder, "--no-cache", "--network=none",
                             "--provenance=false", "--sbom=false", "--platform=linux/amd64",
                             "--build-arg", f"SOURCE_DATE_EPOCH={sde}", *ctx,
                             "-f", os.path.join(root, doc["inputs"]["dockerfile"]),
                             "-o", f"type=oci,dest={out_dir}/rebuilt.oci.tar,rewrite-timestamp=true",
                             os.path.join(root, doc["inputs"]["context"])],
                            capture_output=True, text=True)
        res["build_seconds"] = int(time.time() - t0)
        if bd.returncode:
            res["result"], res["exit"], res["build_tail"] = "build failed", 4, bd.stderr[-1500:]
            return res
        with tarfile.open(os.path.join(out_dir, "rebuilt.oci.tar")) as t:
            got = json.load(t.extractfile("index.json"))["manifests"][0]["digest"]
        res["rebuilt"] = got
        if got != want:
            res["result"], res["exit"] = "rebuilt digest differs", 2
            return res
        if chains:
            res["verified"], res["result"], res["exit"] = True, "verified: bit-for-bit, isolated", 0
        else:
            res["result"], res["exit"] = "digest matches, but the build was NOT isolated (--isolate none)", 3
        return res
    finally:
        _run(["docker", "buildx", "rm", builder])
        if chains:
            _run(["bash", "-c", _UNISOLATE_SH])
        shutil.rmtree(out_dir, ignore_errors=True)
        res["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make"); m.add_argument("dir"); m.add_argument("--write", action="store_true")
    t = sub.add_parser("tree-hash"); t.add_argument("dir")
    for name in ("validate", "verify"):
        p = sub.add_parser(name); p.add_argument("dir"); p.add_argument("--key"); p.add_argument("--signature")
        if name == "verify":
            p.add_argument("--isolate", choices=("container", "host", "none"), default="container")
            p.add_argument("--result", help="write the result JSON here")
    a = ap.parse_args(argv)
    if a.cmd == "make":
        doc = make(a.dir)
        txt = json.dumps(doc, indent=2, sort_keys=True) + "\n"
        if a.write:
            open(os.path.join(a.dir, "rebuild.json"), "w").write(txt)
        print(txt, end="")
        return 0
    if a.cmd == "tree-hash":
        print(tree_hash(a.dir))
        return 0
    if a.cmd == "validate":
        errs = validate(a.dir, a.key, a.signature)
        print("valid" if not errs else "\n".join("INVALID: " + e for e in errs))
        return 0 if not errs else 1
    res = verify(a.dir, a.key, a.signature, a.isolate)
    if a.result:
        json.dump(res, open(a.result, "w"), indent=2)
    print(json.dumps({k: res.get(k) for k in ("result", "subject", "rebuilt", "build_seconds", "egress_self_test")}))
    return res["exit"]


if __name__ == "__main__":
    sys.exit(main())
