import { useState } from "react"
import { Lock, User, KeyRound, Loader2, AlertCircle } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"

interface LoginDialogProps {
  open: boolean
  onSuccess: (token: string, username: string) => void
}

export function LoginDialog({ open, onSuccess }: LoginDialogProps) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password) {
      setError("请输入用户名和密码")
      return
    }

    setError(null)
    setLoading(true)

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data.detail || "用户名或密码错误")
        setLoading(false)
        return
      }

      const data = await res.json()
      onSuccess(data.token, data.username)
    } catch {
      setError("网络连接失败，请稍后重试")
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent
        showCloseButton={false}
        className="sm:max-w-md p-6 rounded-3xl border shadow-2xl bg-card/95 backdrop-blur-md"
      >
        <DialogHeader className="space-y-2 text-center items-center">
          <div className="w-12 h-12 rounded-2xl bg-primary/10 text-primary flex items-center justify-center mb-1">
            <Lock className="w-6 h-6" />
          </div>
          <DialogTitle className="text-lg font-semibold tracking-tight">
            用户登录认证
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            当前系统已启用访问控制，请输入授权账号与密码继续
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 pt-2">
          {error && (
            <div className="p-3 rounded-xl bg-destructive/10 border border-destructive/20 text-destructive text-xs flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="space-y-1.5 text-left">
            <Label className="text-xs text-muted-foreground">用户名</Label>
            <div className="relative">
              <User className="w-4 h-4 text-muted-foreground absolute left-3 top-2.5" />
              <Input
                type="text"
                autoFocus
                placeholder="请输入用户名"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="pl-9 text-xs h-9 rounded-xl bg-background"
                disabled={loading}
              />
            </div>
          </div>

          <div className="space-y-1.5 text-left">
            <Label className="text-xs text-muted-foreground">访问密码</Label>
            <div className="relative">
              <KeyRound className="w-4 h-4 text-muted-foreground absolute left-3 top-2.5" />
              <Input
                type="password"
                placeholder="请输入密码"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="pl-9 text-xs h-9 rounded-xl bg-background"
                disabled={loading}
              />
            </div>
          </div>

          <Button
            type="submit"
            disabled={loading}
            className="w-full h-10 rounded-xl font-medium text-xs mt-2"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
                <span>正在验证...</span>
              </>
            ) : (
              <span>确认登录</span>
            )}
          </Button>

          <p className="text-[11px] text-center text-muted-foreground/80 pt-1">
            账号由服务器管理员在 <code>auth.txt</code> 或环境变量中配置
          </p>
        </form>
      </DialogContent>
    </Dialog>
  )
}
