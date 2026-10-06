/** Shared types + helpers for the task history feature. */

export interface TaskArtifact {
  kind: "mono" | "dual" | "glossary" | "other" | string
  name: string
  size: number
  url: string
}

export interface TranslationTask {
  session_id: string
  status: "running" | "finished" | "failed" | "cancelled" | "interrupted" | string
  filename?: string
  file_id?: string
  size?: number
  lang_in?: string
  lang_out?: string
  engine_type?: string
  created_at?: number
  updated_at?: number
  error?: string | null
  running?: boolean
  source_available?: boolean
  total_bytes?: number
  artifacts: TaskArtifact[]
  cancel_url?: string
  delete_url?: string
}

/**
 * Downloads and the PDF preview iframe now require auth, and neither can send
 * an Authorization header -- so the token rides in the query string, which
 * `get_current_user` already accepts.
 */
export function withAuthToken(url: string | null, token: string | null): string | null {
  if (!url) return null
  if (!token) return url
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`
}

export function formatBytes(bytes: number | undefined): string {
  if (!bytes) return "0 B"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}
