import { describe, expect, test } from "bun:test"
import {
  FALLBACK_BRAIN_URL,
  TOKEN_PLACEHOLDER,
  buildAgentSetupPrompt,
  buildAgentWorkflowPrompt,
  buildCurlExamples,
  buildInstallCommand,
  buildManualCommands,
  isLoopbackUrl,
  normalizeBrainUrl,
} from "../src/lib/connect-templates"

describe("connect-templates", () => {
  test("normalizeBrainUrl trims whitespace and trailing slashes", () => {
    expect(normalizeBrainUrl("  http://100.64.0.10:7622/// ")).toBe("http://100.64.0.10:7622")
    expect(normalizeBrainUrl("   ")).toBe(FALLBACK_BRAIN_URL)
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

  test("buildManualCommands and buildCurlExamples include normalized URL and token", () => {
    const manual = buildManualCommands("http://100.88.1.2:7622/", "fos_tok")
    expect(manual).toContain("http://100.88.1.2:7622/v1/cli/download")
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
})
