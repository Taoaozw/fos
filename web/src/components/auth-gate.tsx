import { useState, type FormEvent } from "react"
import { IconKey, IconBrain } from "@tabler/icons-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"

interface AuthGateProps {
  errorMessage?: string
  onSubmitToken: (token: string) => void
}

export function AuthGate({ errorMessage, onSubmitToken }: AuthGateProps) {
  const [token, setToken] = useState("")

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!token.trim()) return
    onSubmitToken(token.trim())
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <div className="mb-2 flex items-center gap-2 text-muted-foreground">
            <IconBrain className="size-5 text-foreground" />
            <CardTitle className="text-base font-medium">连接 FlowOS 大脑 (fosd)</CardTitle>
          </div>
          <CardDescription>
            请输入 Bearer Token（仅保存在当前浏览器 localStorage，不写入代码或服务端）。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <div className="relative flex items-center">
              <IconKey className="pointer-events-none absolute left-3 size-4 text-muted-foreground" />
              <Input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="fos_..."
                className="pl-9 font-mono text-sm"
                autoFocus
              />
            </div>
            {errorMessage && (
              <div className="text-xs text-destructive">{errorMessage}</div>
            )}
            <Button type="submit" className="w-full">
              连接控制台
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
