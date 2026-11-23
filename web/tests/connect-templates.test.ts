import { describe, expect, test } from "bun:test"
import {
  CLIENT_INGEST_LABEL,
  FALLBACK_BRAIN_URL,
  SESSION_INGEST_SOURCES,
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
  validateBrainUrl,
} from "../src/lib/connect-templates"

describe("connect-templates", () => {
  test("normalizeBrainUrl trims whitespace and trailing slashes", () => {
    expect(normalizeBrainUrl("  http://100.64.0.10:7622/// ")).toBe("http://100.64.0.10:7622")
    expect(normalizeBrainUrl("   ")).toBe(FALLBACK_BRAIN_URL)
  })

  test("resolveDefaultBrainUrl handles schemeless host:5178 without returning null", () => {
    expect(resolveDefaultBrainUrl("http://100.105.149.20:5178")).toBe("http://100.105.149.20:7622")
    expect(resolveDefaultBrainUrl("brain.local:5178")).toBe("http://brain.local:7622")
    expect(resolveDefaultBrainUrl("http://localhost:5178")).toBe("http://localhost:7622")
    expect(resolveDefaultBrainUrl("http://100.105.149.20:7622")).toBe("http://100.105.149.20:7622")
    expect(resolveDefaultBrainUrl("")).toBe(FALLBACK_BRAIN_URL)
  })

  test("validateBrainUrl rejects query strings, fragments, and unsupported schemes", () => {
    expect(validateBrainUrl("http://100.105.149.20:7622")).toBeNull()
    expect(validateBrainUrl("https://fos.example.com")).toBeNull()
    expect(validateBrainUrl("http://100.105.149.20:7622?foo=1")).not.toBeNull()
    expect(validateBrainUrl("http://100.105.149.20:7622#frag")).not.toBeNull()
    expect(validateBrainUrl("ftp://100.105.149.20:7622")).not.toBeNull()
  })

  test("isViteDevUrl detects port 5178 even without scheme", () => {
    expect(isViteDevUrl("http://100.105.149.20:5178")).toBe(true)
    expect(isViteDevUrl("brain.local:5178")).toBe(true)
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

  test("buildInstallCommand uses fail-fast mktemp and single-quotes unsafe tokens/IPv6 URLs", () => {
    const cmdPlaceholder = buildInstallCommand("http://100.88.1.2:7622/")
    expect(cmdPlaceholder).toContain("tmp=$(mktemp)")
    expect(cmdPlaceholder).toContain("http://100.88.1.2:7622/install.sh")
    expect(cmdPlaceholder).toContain(`'${TOKEN_PLACEHOLDER}'`)

    const cmdIpv6 = buildInstallCommand("http://[::1]:7622", "tok$(id)")
    expect(cmdIpv6).toContain("'http://[::1]:7622/install.sh'")
    expect(cmdIpv6).toContain("'tok$(id)'")
  })

  test("buildManualCommands and buildCurlExamples single-quote auth header and use mktemp -d", () => {
    const manual = buildManualCommands("http://100.88.1.2:7622/", "fos_$(whoami)", "0.1.2")
    expect(manual).toContain("tmp_dir=$(mktemp -d)")
    expect(manual).toContain("'Authorization: Bearer fos_$(whoami)'")
    expect(manual).toContain("fos-0.1.2-py3-none-any.whl")
    expect(manual).not.toContain("fos-latest")
    expect(manual).toContain("fos init --brain http://100.88.1.2:7622 --token 'fos_$(whoami)'")

    const curl = buildCurlExamples("http://100.88.1.2:7622/", "fos_tok")
    expect(curl).toContain("'Authorization: Bearer fos_tok'")
    expect(curl).toContain("'http://100.88.1.2:7622/v1/boot?budget=300'")
  })

  test("buildAgentSetupPrompt includes safety rule, explicit session sources, and placeholder note", () => {
    const withPlaceholder = buildAgentSetupPrompt("http://100.88.1.2:7622")
    expect(withPlaceholder).toContain("如未填入请先向我索取")
    expect(withPlaceholder).toContain("http://100.88.1.2:7622/v1/health")
    expect(withPlaceholder).toContain(SESSION_INGEST_SOURCES)
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

  test("remote ingest templates quote paths with spaces, use explicit session sources, and escape plist XML", () => {
    const manual = buildRemoteIngestManualCommands("~/Client Projects")
    expect(manual).toContain(SESSION_INGEST_SOURCES)
    expect(manual).not.toContain("--from all")
    expect(manual).toContain('"$HOME"/\'Client Projects\'')

    const launchd = buildRemoteIngestLaunchdScript("~/Client Projects")
    expect(launchd).toContain(CLIENT_INGEST_LABEL)
    expect(launchd).toContain("plistlib")
    expect(launchd).toContain("plutil -lint")
    expect(launchd).toContain("'~/Client Projects'")

    const cron = buildRemoteIngestCronCommand("~/Client Projects")
    expect(cron).toContain("fos-client-ingest.sh")
    expect(cron).toContain("# fos-client-ingest$")
    expect(cron).not.toContain("--from all")
  })

  test("unbind, full uninstall, and agent unbind prompt stop scheduler first and gate cache deletion on remaining_outbox:0", () => {
    const unbind = buildUnbindOnlyCommands()
    expect(unbind.indexOf("launchctl unload")).toBeLessThan(unbind.indexOf("fos sync --json"))
    expect(unbind).toContain('remaining_outbox":0')
    expect(unbind).toContain("# fos-client-ingest$")
    expect(unbind).toContain('FOS_HOME="${FOS_HOME:-$HOME/.fos}"')
    expect(unbind).not.toContain("uv tool uninstall fos")

    const uninstall = buildFullUninstallCommands()
    expect(uninstall).toContain('remaining_outbox":0')
    expect(uninstall).toContain("uv tool uninstall fos")
    expect(uninstall).toContain('"$HOME/.claude/skills/fos"')

    const prompt = buildAgentUnbindPrompt()
    expect(prompt).toContain("仅解绑远端大脑")
    expect(prompt).toContain("完全卸载")
    expect(prompt).toContain("remaining_outbox == 0")
    expect(prompt).toContain("FOS_BRAIN_URL")
  })
})
