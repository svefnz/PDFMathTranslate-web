import { useCallback, useEffect, useState } from "react"
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Download,
  FileText,
  HardDrive,
  Loader2,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  errorMessage,
  formatBytes,
  withAuthToken,
  type TranslationTask,
} from "@/types/tasks"

interface TaskHistoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  authToken: string | null
  /** Bumped by the parent after a translation finishes, to force a refresh. */
  refreshKey?: number
  onLoadTask?: (task: TranslationTask) => void
}

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  running: { label: "翻译中", className: "bg-primary/10 text-primary border-primary/30" },
  finished: {
    label: "已完成",
    className: "bg-emerald-500/10 text-emerald-600 border-emerald-500/30",
  },
  failed: { label: "失败", className: "bg-destructive/10 text-destructive border-destructive/30" },
  cancelled: { label: "已取消", className: "bg-muted text-muted-foreground border-border" },
  interrupted: { label: "已中断", className: "bg-amber-500/10 text-amber-600 border-amber-500/30" },
}

const ARTIFACT_LABEL: Record<string, string> = {
  mono: "单语译文",
  dual: "双语对照",
  glossary: "术语表",
  other: "其他文件",
}

function formatTime(seconds: number | undefined): string {
  if (!seconds) return "-"
  const date = new Date(seconds * 1000)
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function TaskHistoryDialog({
  open,
  onOpenChange,
  authToken,
  refreshKey = 0,
  onLoadTask,
}: TaskHistoryDialogProps) {
  const [tasks, setTasks] = useState<TranslationTask[]>([])
  const [total, setTotal] = useState(0)
  const [totalBytes, setTotalBytes] = useState(0)
  const [retentionDays, setRetentionDays] = useState(0)
  const [loading, setLoading] = useState(false)
  const [loadedOnce, setLoadedOnce] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const authHeaders = useCallback((): Record<string, string> => {
    return authToken ? { Authorization: `Bearer ${authToken}` } : {}
  }, [authToken])

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/tasks?limit=200", { headers: authHeaders() })
      if (res.status === 401) {
        setError("访问需要认证，请先登录")
        return
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setTasks(data.tasks || [])
      setTotal(data.total || 0)
      setTotalBytes(data.total_bytes || 0)
      setRetentionDays(data.retention_days || 0)
      setError(null)
    } catch (err: unknown) {
      setError(errorMessage(err, "加载历史记录失败"))
    } finally {
      setLoadedOnce(true)
      setLoading(false)
    }
  }, [authHeaders])

  const refresh = useCallback(() => {
    setLoading(true)
    void load()
  }, [load])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    void (async () => {
      if (!cancelled) await load()
    })()
    return () => {
      cancelled = true
    }
  }, [open, refreshKey, load])

  // While something is translating, keep the list fresh so status flips live.
  useEffect(() => {
    if (!open) return
    const hasRunning = tasks.some((task) => task.running)
    if (!hasRunning) return
    const timer = setInterval(() => void load(), 5000)
    return () => clearInterval(timer)
  }, [open, tasks, load])

  const handleDelete = async (task: TranslationTask) => {
    if (!confirm(`确定删除「${task.filename || task.session_id}」的译文与原始文件？此操作不可恢复。`)) {
      return
    }
    setBusyId(task.session_id)
    setNotice(null)
    try {
      const res = await fetch(task.delete_url || `/api/tasks/${task.session_id}`, {
        method: "DELETE",
        headers: authHeaders(),
      })
      const body = await res.json().catch(() => null)
      if (!res.ok) {
        setError(body?.detail || `删除失败 (HTTP ${res.status})`)
      } else {
        setNotice(`已删除，释放 ${formatBytes(body?.freed_bytes)}`)
        await load()
      }
    } catch (err: unknown) {
      setError(errorMessage(err, "删除失败"))
    } finally {
      setBusyId(null)
    }
  }

  const handleCancel = async (task: TranslationTask) => {
    setBusyId(task.session_id)
    try {
      await fetch(task.cancel_url || `/api/cancel/${task.session_id}`, {
        method: "POST",
        headers: authHeaders(),
      })
      await load()
    } finally {
      setBusyId(null)
    }
  }

  const handleCleanup = async () => {
    const days = retentionDays > 0 ? retentionDays : 30
    // dry run first: never delete a pile of documents without saying how many
    setBusyId("__cleanup__")
    setNotice(null)
    setError(null)
    try {
      const res = await fetch("/api/tasks/cleanup", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ older_than_days: days, dry_run: true }),
      })
      const body = await res.json().catch(() => null)
      if (!res.ok) {
        setError(body?.detail || `清理失败 (HTTP ${res.status})`)
        return
      }
      if (!body?.tasks) {
        setNotice(`${days} 天内没有可清理的任务`)
        return
      }
      const ok = confirm(
        `将删除 ${body.tasks} 个超过 ${days} 天的任务，释放约 ${formatBytes(body.freed_bytes)}。继续？`
      )
      if (!ok) return
      const run = await fetch("/api/tasks/cleanup", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ older_than_days: days }),
      })
      const runBody = await run.json().catch(() => null)
      if (!run.ok) {
        setError(runBody?.detail || `清理失败 (HTTP ${run.status})`)
        return
      }
      setNotice(
        `已清理 ${runBody.tasks} 个任务、${runBody.orphan_uploads || 0} 个未翻译上传，释放 ${formatBytes(runBody.freed_bytes)}`
      )
      await load()
    } catch (err: unknown) {
      setError(errorMessage(err, "清理失败"))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col p-0 gap-0">
        <DialogHeader className="px-6 pt-6 pb-3 border-b shrink-0">
          <DialogTitle className="text-base flex items-center gap-2">
            <Clock className="w-4 h-4 text-primary" />
            <span>历史任务</span>
            {total > 0 && (
              <Badge variant="secondary" className="text-[10px] rounded-full px-2 py-0">
                {total}
              </Badge>
            )}
          </DialogTitle>
          <DialogDescription className="text-xs flex items-center gap-3">
            <span className="inline-flex items-center gap-1">
              <HardDrive className="w-3 h-3" />
              占用 {formatBytes(totalBytes)}
            </span>
            <span>
              保留策略：
              {retentionDays > 0 ? `${retentionDays} 天（重启时自动清理）` : "未启用"}
            </span>
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2 px-6 py-2.5 border-b shrink-0">
          <Button
            variant="outline"
            size="sm"
            className="h-8 rounded-lg gap-1.5 text-xs"
            onClick={refresh}
            disabled={loading}
          >
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            <span>刷新</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8 rounded-lg gap-1.5 text-xs"
            onClick={() => void handleCleanup()}
            disabled={busyId === "__cleanup__"}
          >
            {busyId === "__cleanup__" ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Trash2 className="w-3.5 h-3.5" />
            )}
            <span>清理旧任务</span>
          </Button>
          <span className="text-[11px] text-muted-foreground ml-auto">
            删除会同时移除译文与原始文件，不可恢复
          </span>
        </div>

        {(error || notice) && (
          <div className="px-6 py-2 shrink-0">
            {error && (
              <p className="text-xs text-destructive flex items-start gap-1.5">
                <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>{error}</span>
              </p>
            )}
            {notice && (
              <p className="text-xs text-emerald-600 flex items-start gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>{notice}</span>
              </p>
            )}
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-6 py-3 min-h-[240px]">
          {!loadedOnce && tasks.length === 0 ? (
            <div className="flex items-center justify-center gap-2 py-16 text-xs text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>正在加载历史记录...</span>
            </div>
          ) : tasks.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground">
              <div className="w-14 h-14 rounded-2xl bg-muted/60 flex items-center justify-center mb-3">
                <FileText className="w-7 h-7 text-muted-foreground/50" />
              </div>
              <p className="text-sm font-medium text-foreground">暂无历史任务</p>
              <p className="text-xs mt-1">完成一次翻译后，这里会显示状态、产物与占用空间。</p>
            </div>
          ) : (
            <ul className="space-y-2">
              {tasks.map((task) => {
                const style = STATUS_STYLE[task.status] || {
                  label: task.status,
                  className: "bg-muted text-muted-foreground border-border",
                }
                const busy = busyId === task.session_id
                return (
                  <li
                    key={task.session_id}
                    className="rounded-xl border bg-card p-3 flex flex-col gap-2"
                  >
                    <div className="flex items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium truncate flex items-center gap-2">
                          <span className="truncate">{task.filename || "(未知文件)"}</span>
                          <Badge
                            variant="outline"
                            className={`text-[10px] rounded-full px-2 py-0 shrink-0 ${style.className}`}
                          >
                            {style.label}
                          </Badge>
                        </p>
                        <p className="text-[11px] text-muted-foreground mt-0.5 flex flex-wrap gap-x-2.5 gap-y-0.5">
                          <span>{formatTime(task.created_at)}</span>
                          <span>
                            {task.lang_in || "?"} → {task.lang_out || "?"}
                          </span>
                          {task.engine_type && <span>{task.engine_type}</span>}
                          <span>{formatBytes(task.total_bytes)}</span>
                          {task.status === "finished" && task.source_available === false && (
                            <span className="text-amber-600">原文已删除</span>
                          )}
                        </p>
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        {task.running && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 rounded-lg text-[11px] px-2"
                            onClick={() => void handleCancel(task)}
                            disabled={busy}
                          >
                            取消
                          </Button>
                        )}
                        {task.artifacts.length > 0 && !task.running && onLoadTask && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 rounded-lg text-[11px] px-2"
                            onClick={() => onLoadTask(task)}
                          >
                            载入预览
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 rounded-lg text-[11px] px-2 text-destructive hover:text-destructive"
                          onClick={() => void handleDelete(task)}
                          disabled={busy || task.running}
                          title={task.running ? "请先取消运行中的任务" : "删除任务"}
                        >
                          {busy ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="w-3.5 h-3.5" />
                          )}
                        </Button>
                      </div>
                    </div>

                    {task.error && (
                      <p className="text-[11px] text-destructive/90 bg-destructive/5 rounded-lg px-2 py-1.5 flex items-start gap-1.5">
                        <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                        <span className="line-clamp-3">{task.error}</span>
                      </p>
                    )}

                    {task.artifacts.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {task.artifacts.map((artifact) => (
                          <a
                            key={artifact.name}
                            href={withAuthToken(artifact.url, authToken) || undefined}
                            download
                            target="_blank"
                            rel="noreferrer"
                          >
                            <Button
                              size="sm"
                              variant="secondary"
                              className="h-7 rounded-lg gap-1.5 text-[11px] px-2"
                            >
                              <Download className="w-3 h-3" />
                              <span>
                                {ARTIFACT_LABEL[artifact.kind] || artifact.kind}
                                <span className="text-muted-foreground ml-1">
                                  {formatBytes(artifact.size)}
                                </span>
                              </span>
                            </Button>
                          </a>
                        ))}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
