"""Unit & integration tests for Code Pattern / Exemplar Memory (symbol freshness + SemVer compat drift)."""

from __future__ import annotations

import json
from pathlib import Path

from fos.cli import run_cli
from fos.client import FosClient
from fos.code_pattern import (
    check_compat_drift,
    detect_repo_stack_versions,
    extract_code_symbol,
)
from fos.config import FosConfig, save_config
from fos.freshness import check_source_freshness


def _sample_kotlin_repo(root: Path, *, spring_boot_ver: str = "3.3.5", kotlin_ver: str = "2.1.0") -> Path:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    gradle_dir = root / "gradle"
    gradle_dir.mkdir(parents=True, exist_ok=True)
    (gradle_dir / "libs.versions.toml").write_text(
        f"""[versions]
kotlin = "{kotlin_ver}"
spring-boot = "{spring_boot_ver}"

[libraries]
spring-boot-starter-webflux = {{ module = "org.springframework.boot:spring-boot-starter-webflux" }}
""",
        encoding="utf-8",
    )
    src_dir = root / "src" / "main" / "kotlin"
    src_dir.mkdir(parents=True, exist_ok=True)
    kt_file = src_dir / "ZeroCopyRelay.kt"
    kt_file.write_text(
        """package com.example.gateway

/** Zero-copy DataBuffer relay with upstream drain on client cancel. */
class DetachableBody(private val upstream: Any) {
    fun attach(): Int {
        val x = 1
        return x + 1
    }
}

fun unrelatedHelper(v: Int): Int {
    return v * 2
}
""",
        encoding="utf-8",
    )
    return kt_file


def test_manifest_stack_version_detection(tmp_path: Path) -> None:
    # Gradle libs.versions.toml
    kt_repo = tmp_path / "kt-repo"
    _sample_kotlin_repo(kt_repo, spring_boot_ver="4.1.1", kotlin_ver="2.4.21")
    stack, mf = detect_repo_stack_versions(kt_repo)
    assert mf == "gradle/libs.versions.toml"
    assert stack["kotlin"] == "2.4"
    assert stack["spring-boot"] == "4.1"
    assert stack["webflux"] == "4.1"

    # package.json
    web_repo = tmp_path / "web-repo"
    (web_repo / ".git").mkdir(parents=True)
    (web_repo / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^19.1.0", "vite": "~6.2.1", "tailwindcss": "4.0.9"}}),
        encoding="utf-8",
    )
    w_stack, w_mf = detect_repo_stack_versions(web_repo)
    assert w_mf == "package.json"
    assert w_stack == {"react": "19.1", "vite": "6.2", "tailwindcss": "4.0"}

    # go.mod
    go_repo = tmp_path / "go-repo"
    (go_repo / ".git").mkdir(parents=True)
    (go_repo / "go.mod").write_text("module example.com/app\n\ngo 1.24.1\n\nrequire github.com/gin-gonic/gin v1.10.0\n", encoding="utf-8")
    g_stack, g_mf = detect_repo_stack_versions(go_repo)
    assert g_mf == "go.mod"
    assert g_stack == {"go": "1.24", "gin": "1.10"}


def test_symbol_extraction_and_isolated_freshness(tmp_path: Path) -> None:
    kt_file = _sample_kotlin_repo(tmp_path / "gw")
    ext = extract_code_symbol(kt_file, "DetachableBody")
    assert ext["found"] is True
    assert ext["lang"] == "kotlin"
    assert "class DetachableBody" in ext["snippet"]
    assert "unrelatedHelper" not in ext["snippet"]

    ref = f"{kt_file.resolve()}::DetachableBody"
    h = ext["hash"]
    assert check_source_freshness(ref, h) == "fresh"

    # Modifying an UNRELATED function in the same file MUST stay fresh!
    orig = kt_file.read_text(encoding="utf-8")
    kt_file.write_text(orig.replace("return v * 2", "return v * 42"), encoding="utf-8")
    assert check_source_freshness(ref, h) == "fresh"

    # Modifying the anchored symbol body marks it drifted
    kt_file.write_text(kt_file.read_text(encoding="utf-8").replace("return x + 1", "return x + 99"), encoding="utf-8")
    assert check_source_freshness(ref, h) == "drifted"

    # Deleting the anchored symbol marks it broken_ref
    kt_file.write_text("package com.example.gateway\n\nfun unrelatedHelper(v: Int): Int = v\n", encoding="utf-8")
    assert check_source_freshness(ref, h) == "broken_ref"


def test_code_pattern_e2e_compat_drift_and_rebuild(tmp_path: Path) -> None:
    home = tmp_path / "fos_home"
    save_config(FosConfig(mode="local", device="test"), home)
    client = FosClient(home=home)

    # Capture exemplar from Spring Boot 3.3 / Kotlin 2.1 repo
    old_repo = tmp_path / "gateway-v3"
    kt_file = _sample_kotlin_repo(old_repo, spring_boot_ver="3.3.5", kotlin_ver="2.1.0")

    added = client.add_pattern(
        f"{kt_file}::DetachableBody",
        "WebFlux 流式代理使用无锁 BaseSubscriber<DataBuffer> 零拷贝透传并在客户端断开后继续排空上游",
        rationale="避免堆内存拷贝并保证断连请求的 usage 计费完整",
        bench_cmd="./gradlew :services:gateway:test --tests '*DetachableBodyTest*'",
        trigger_when=["WebFlux 零拷贝流式转发", "客户端断连后继续计费"],
    )
    node = added["node"]
    assert node["scope"] == "gateway-v3"
    assert node["severity"] == "P1"
    assert node["source_ref"] == f"{kt_file.resolve()}::DetachableBody"
    assert node["compat"]["pattern"] == "code_exemplar"
    assert node["compat"]["lang"] == "kotlin"
    assert node["compat"]["stack"]["spring-boot"] == "3.3"
    assert node["compat"]["stack"]["kotlin"] == "2.1"

    # P1 code exemplars must NOT pollute L0 boot kernel
    boot = client.boot(project="gateway-v3", budget=300)
    assert all(e["id"] != node["id"] for e in boot["entries"])

    # L2 verbatim evidence preserves compact Kotlin skeleton
    detail = client.get(node["id"], tier="L2")
    assert "```kotlin" in detail["evidence"][0]["raw"]
    assert "class DetachableBody" in detail["evidence"][0]["raw"]

    # Recall in same-major repo -> compatible
    rec_same = client.recall(
        "WebFlux zero-copy DataBuffer streaming",
        cwd_stack={"kotlin": "2.1", "spring-boot": "3.3"},
    )
    hit_same = next(r for r in rec_same["results"] if r["id"] == node["id"])
    assert hit_same["compat_check"]["status"] == "compatible"
    assert hit_same["freshness"] == "fresh"

    # Recall in newer major framework repo (Spring Boot 4.1) -> STILL recalled with major_drift advisory!
    rec_new = client.recall(
        "WebFlux zero-copy DataBuffer streaming",
        cwd_stack={"kotlin": "2.4", "spring-boot": "4.1"},
    )
    hit_new = next(r for r in rec_new["results"] if r["id"] == node["id"])
    assert hit_new["compat_check"]["status"] == "major_drift"
    assert "spring-boot 3.3 -> 4.1" in hit_new["compat_check"]["drifts"]
    assert "跨主版本" in hit_new["compat_check"]["note"]

    # Recall in Go repo -> cross_stack
    cc_go = check_compat_drift(node["compat"], {"go": "1.24"})
    assert cc_go == {"status": "cross_stack"}

    # Rebuild from immutable ledger preserves compat + source_ref + source_hash
    rebuilt = client.rebuild()
    assert rebuilt["node_count"] >= 1
    listed = client.list_patterns(cwd_stack={"kotlin": "2.4", "spring-boot": "4.1"})
    assert listed["count"] == 1
    assert listed["patterns"][0]["compat"]["stack"]["spring-boot"] == "3.3"
    assert listed["patterns"][0]["compat_check"]["status"] == "major_drift"


def test_cli_pattern_add_and_list(tmp_path: Path, capsys) -> None:
    home = tmp_path / "fos_cli"
    save_config(FosConfig(mode="local", device="test"), home)
    kt_file = _sample_kotlin_repo(tmp_path / "my-gw", spring_boot_ver="4.1.1", kotlin_ver="2.4.21")

    rc1 = run_cli(
        [
            "--home",
            str(home),
            "pattern",
            "add",
            f"{kt_file}::DetachableBody",
            "WebFlux 无锁 DataBuffer 零拷贝透传与断连续读",
            "--why",
            "零堆拷贝且保证上游 usage 完整计费",
            "--bench",
            "./gradlew test",
            "--json",
        ]
    )
    assert rc1 == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["node"]["compat"]["stack"]["spring-boot"] == "4.1"

    rc2 = run_cli(["--home", str(home), "pattern", "list", "--json"])
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["count"] == 1
    assert out2["patterns"][0]["freshness"] == "fresh"

    rc3 = run_cli(["describe", "pattern"])
    assert rc3 == 0
    desc = json.loads(capsys.readouterr().out)
    assert desc["name"] == "pattern"


def test_pattern_sync_evolution_and_antipattern_demotion(tmp_path: Path, capsys) -> None:
    home = tmp_path / "fos_evolve"
    save_config(FosConfig(mode="local", device="test"), home)
    client = FosClient(home=home)

    kt_v1 = _sample_kotlin_repo(tmp_path / "gw-v1", spring_boot_ver="3.3.5", kotlin_ver="2.1.0")
    added = client.add_pattern(
        f"{kt_v1}::DetachableBody",
        "WebFlux 流式代理用无锁 BaseSubscriber<DataBuffer> 零拷贝透传",
        rationale="v1 初始实现",
    )
    id_v1 = added["node"]["id"]

    # 1. Sync when nothing changed -> idempotent no-op
    noop = client.sync_pattern(id_v1)
    assert noop["changed"] is False
    assert noop["freshness"] == "fresh"

    # 2. Optimize the symbol in-place -> drifted -> sync evolves to v2 superseding v1
    kt_v1.write_text(
        kt_v1.read_text(encoding="utf-8").replace("return x + 1", "return x + 2 // zero-alloc fast path"),
        encoding="utf-8",
    )
    assert check_source_freshness(added["node"]["source_ref"], added["node"]["source_hash"]) == "drifted"

    rc_sync = run_cli(
        [
            "--home",
            str(home),
            "pattern",
            "sync",
            id_v1,
            "--why",
            "消除热路径额外分配，0 B/op",
            "--json",
        ]
    )
    assert rc_sync == 0
    synced = json.loads(capsys.readouterr().out)
    assert synced["changed"] is True
    assert synced["superseded"] == id_v1
    id_v2 = synced["node"]["id"]
    assert id_v2 != id_v1
    assert synced["node"]["rationale"] == "消除热路径额外分配，0 B/op"

    # v1 is now superseded; only v2 is active and fresh with updated L2 skeleton
    old_detail = client.get(id_v1, tier="L1")
    assert old_detail["node"]["status"] == "superseded"
    assert old_detail["node"]["superseded_by"] == id_v2

    new_detail = client.get(id_v2, tier="L2", include_graph=True)
    assert "zero-alloc fast path" in new_detail["evidence"][0]["raw"]
    assert any(e["edge_type"] == "SUPERSEDES" and e["dst_id"] == id_v1 for e in new_detail["graph"])

    # 3. Cross-repo evolution via `pattern add --supersedes`
    kt_v3 = _sample_kotlin_repo(tmp_path / "gw-v3", spring_boot_ver="4.1.1", kotlin_ver="2.4.21")
    rc_v3 = run_cli(
        [
            "--home",
            str(home),
            "pattern",
            "add",
            f"{kt_v3}::DetachableBody",
            "WebFlux 4.1 零拷贝透传使用新版原生排空接口",
            "--supersedes",
            id_v2,
            "--why",
            "在 Spring Boot 4.1 中进一步简化状态机",
            "--json",
        ]
    )
    assert rc_v3 == 0
    out_v3 = json.loads(capsys.readouterr().out)
    assert out_v3["superseded"] == id_v2
    id_v3 = out_v3["node"]["id"]

    # Active patterns list only shows the latest evolved v3 (v1 & v2 cleanly retired)
    plist = client.list_patterns()
    assert plist["count"] == 1
    assert plist["patterns"][0]["id"] == id_v3
    assert plist["patterns"][0]["freshness"] == "fresh"

    # 4. If a pattern turns out to have a fatal bug -> demote into anti-pattern lesson superseding v3
    demoted = client.add(
        kind="lesson",
        scope="gw-v3",
        severity="P0",
        supersedes=id_v3,
        content="禁止在 cancel 回调里同步调用 requestUnbounded，会导致重入栈溢出；改用异步排空",
    )
    assert demoted["superseded"] == id_v3
    assert client.list_patterns()["count"] == 0
    assert client.get(id_v3)["node"]["status"] == "superseded"
    # Rebuild preserves the full evolution & demotion chain
    client.rebuild()
    assert client.get(id_v1)["node"]["status"] == "superseded"
    assert client.get(id_v2)["node"]["status"] == "superseded"
    assert client.get(id_v3)["node"]["status"] == "superseded"
    assert client.get(demoted["node"]["id"])["node"]["status"] == "active"
