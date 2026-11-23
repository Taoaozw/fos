import { describe, expect, test } from "bun:test"
import {
  CLIENT_INGEST_LABEL,
  FALLBACK_BRAIN_URL,
  TOKEN_PLACEHOLDER,
  buildAgentSetupPrompt,
  buildAgentUnbindPrompt,
  buildAgentWorkflowPrompt,
  buildCurlExamples,
  buildFullUninstallCommands,
  buildInstallCommand,
  buildManualCommands,
  buildRemoteIngestCronCommand,
  buildRemoteIngestLaunchdScript,
  buildRemoteIngestManualCommands,
  buildUnbindOnlyCommands,
  isLoopbackUrl,
  isViteDevUrl,
  normalizeBrainUrl,
  resolveDefaultBrainUrl,
} from "../src/lib/connect-templates"

describe("connect-templates", () => {
  test("normalizeBrainUrl trims whitespace and trailing slashes", () => {
    expect(normalizeBrainUrl("  http://100.64.0.10:7622/// ")).toBe("http://100.64.0.10:7622")
    expect(normalizeBrainUrl("   ")).toBe(FALLBACK_BRAIN_URL)
  })

  test("resolveDefaultBrainUrl rewrites Vite dev port 5178 to fosd port 7622", () => {
    expect(resolveDefaultBrainUrl("http://100.105.149.20:5178")).toBe("http://100.105.149.20:7622")
    expect(resolveDefaultBrainUrl("http://localhost:5178")).toBe("http://localhost:7622")
    expect(resolveDefaultBrainUrl("http://100.105.149.20:7622")).toBe("http://100.105.149.20:7622")
    expect(resolveDefaultBrainUrl("")).toBe(FALLBACK_BRAIN_URL)
  })

  test("isViteDevUrl detects port 5178", () => {
    expect(isViteDevUrl("http://100.105.149.20:5178")).toBe(true)
    expect(isViteDevUrl("http://100.105.149.20:7622")).toBe(false)
  })

  test("isLoopbackUrl detects localhost / 127.0.0.1 / ::1 and allows LAN or Tailscale IPs", () => {
    expect(isLoopbackUrl("http://localhost:7622")).toBe(true)
    expect(isLoopbackUrl("http://127.0.0.1:7622")).toBe(true)
    expect(isLoopbackUrl("http://0.0.0.0:7622")).toBe(true)
    expect(isLoopbackUrl("http://[::1]:7622")).toBe(true)
    expect(isLoopbackUrl("http://100.88.1.2:7622")).toBe(false)
    expect(isLoopbackUrl("http://192.168.1.20:7622")).toBe(false)
    expect(isLoopbackUrl("https://fos.example.com")).toBe(false)
  })

  test("buildInstallCommand defaults to placeholder token and quotes special tokens", () => {
    expect(buildInstallCommand("http://100.88.1.2:7622/")).toBe(
      `curl -fsSL http://100.88.1.2:7622/install.sh | sh -s -- '${TOKEN_PLACEHOLDER}'`
    )
    expect(buildInstallCommand("http://100.88.1.2:7622", "fos_abc123")).toBe(
      "curl -fsSL http://100.88.1.2:7622/install.sh | sh -s -- fos_abc123"
    )
  })

  test("buildManualCommands and buildCurlExamples include normalized URL, PEP-427 wheel filename, and token", () => {
    const manual = buildManualCommands("http://100.88.1.2:7622/", "fos_tok", "0.2.3")
    expect(manual).toContain("http://100.88.1.2:7622/v1/cli/download")
    expect(manual).toContain("/tmp/fos-0.2.3-py3-none-any.whl")
    expect(manual).not.toContain("fos-latest")
    expect(manual).toContain("fos init --brain http://100.88.1.2:7622 --token fos_tok")
    expect(manual).toContain("fos skill install")

    const curl = buildCurlExamples("http://100.88.1.2:7622/", "fos_tok")
    expect(curl).toContain('Authorization: Bearer fos_tok')
    expect(curl).toContain("http://100.88.1.2:7622/v1/boot?budget=300")
  })

  test("buildAgentSetupPrompt includes safety rule, install command, and placeholder note when token omitted", () => {
    const withPlaceholder = buildAgentSetupPrompt("http://100.88.1.2:7622")
    expect(withPlaceholder).toContain("如未填入请先向我索取")
    expect(withPlaceholder).toContain("http://100.88.1.2:7622/v1/health")
    expect(withPlaceholder).toContain("fos skill install --dir")
    expect(withPlaceholder).toContain("严禁")

    const withRealToken = buildAgentSetupPrompt("http://100.88.1.2:7622", "fos_real_123")
    expect(withRealToken).not.toContain("如未填入请先向我索取")
    expect(withRealToken).toContain("fos_real_123")
  })

  test("buildAgentWorkflowPrompt covers core fos lifecycle commands", () => {
    const wf = buildAgentWorkflowPrompt()
    expect(wf).toContain("fos boot")
    expect(wf).toContain("fos mirror")
    expect(wf).toContain("fos recall")
    expect(wf).toContain("fos add")
    expect(wf).toContain("fos sync")
  })

  test("remote ingest templates include git root, client ingest label, and sync without dream", () => {
    const manual = buildRemoteIngestManualCommands("~/work")
    expect(manual).toContain("fos ingest --from all --since 48h --json")
    expect(manual).toContain("fos ingest --from git --root ~/work --since 48h --limit 30 --json")
    expect(manual).toContain("fos sync --json")

    const launchd = buildRemoteIngestLaunchdScript("~/work")
    expect(launchd).toContain(CLIENT_INGEST_LABEL)
    expect(launchd).toContain("launchctl load")
    expect(launchd).toContain("$HOME/work")

    const cron = buildRemoteIngestCronCommand("~/work")
    expect(cron).toContain("# fos-client-ingest")
    expect(cron).toContain("crontab")
    expect(cron).toContain("$HOME/work")
  })

  test("unbind, full uninstall, and agent unbind prompt flush outbox and clean schedules/token", () => {
    const unbind = buildUnbindOnlyCommands()
    expect(unbind).toContain("fos sync --json || true")
    expect(unbind).toContain(CLIENT_INGEST_LABEL)
    expect(unbind).toContain("rm -f ~/.fos/config.json")
    expect(unbind).not.toContain("uv tool uninstall fos")

    const uninstall = buildFullUninstallCommands()
    expect(uninstall).toContain("fos sync --json || true")
    expect(uninstall).toContain("uv tool uninstall fos")
    expect(uninstall).toContain("~/.claude/skills/fos")
    expect(uninstall).toContain("rm -rf ~/.fos")

    const prompt = buildAgentUnbindPrompt()
    expect(prompt).toContain("仅解绑远端大脑")
    expect(prompt).toContain("完全卸载")
    expect(prompt).toContain("fos sync --json")
    expect(prompt).toContain(CLIENT_INGEST_LABEL)
  })
})
