import { useState, useRef, useEffect } from "react"
import {
  FileText,
  Upload,
  ArrowRightLeft,
  Settings2,
  Sparkles,
  Download,
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Moon,
  Sun,
  BookOpen,
  Loader2,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

interface UploadedFileInfo {
  fileId: string
  filename: string
  size: number
}

interface TranslationResult {
  monoUrl: string | null
  dualUrl: string | null
  glossaryUrl: string | null
}

const LANGUAGES = [
  { label: "英语 (English)", value: "en" },
  { label: "简体中文 (Simplified Chinese)", value: "zh-CN" },
  { label: "繁体中文 (Traditional Chinese)", value: "zh-TW" },
  { label: "日语 (Japanese)", value: "ja" },
  { label: "韩语 (Korean)", value: "ko" },
  { label: "德语 (German)", value: "de" },
  { label: "法语 (French)", value: "fr" },
  { label: "俄语 (Russian)", value: "ru" },
  { label: "西班牙语 (Spanish)", value: "es" },
  { label: "意大利语 (Italian)", value: "it" },
]

const POPULAR_ENGINES = [
  { id: "OpenAI", name: "OpenAI", defaultModel: "gpt-4o-mini", needKey: true },
  { id: "DeepSeek", name: "DeepSeek", defaultModel: "deepseek-chat", needKey: true },
  { id: "SiliconFlow", name: "SiliconFlow (硅基流动)", defaultModel: "Qwen/Qwen2.5-7B-Instruct", needKey: true },
  { id: "Ollama", name: "Ollama (本地私有化)", defaultModel: "qwen2.5", needKey: false },
  { id: "Google", name: "Google Translate (免Key)", defaultModel: "", needKey: false },
  { id: "Bing", name: "Bing (微软免费)", defaultModel: "", needKey: false },
]

function GithubIcon({ className }: { className?: string }) {
  return (
    <svg role="img" viewBox="0 0 24 24" fill="currentColor" className={className} xmlns="http://www.w3.org/2000/svg">
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
    </svg>
  )
}

export function App() {
  // Dark mode
  const [isDark, setIsDark] = useState(false)
  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add("dark")
    } else {
      document.documentElement.classList.remove("dark")
    }
  }, [isDark])

  // File state
  const [file, setFile] = useState<UploadedFileInfo | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Options
  const [langIn, setLangIn] = useState("en")
  const [langOut, setLangOut] = useState("zh-CN")
  const [engineType, setEngineType] = useState("OpenAI")
  const [apiKey, setApiKey] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [modelName, setModelName] = useState("gpt-4o-mini")
  const [showAdvanced, setShowAdvanced] = useState(false)

  // Translation execution state
  const [isTranslating, setIsTranslating] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [progress, setProgress] = useState(0)
  const [stage, setStage] = useState("")
  const [stageDetail, setStageDetail] = useState("")
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [result, setResult] = useState<TranslationResult | null>(null)

  // Preview tab: dual | mono
  const [previewTab, setPreviewTab] = useState<"dual" | "mono">("dual")

  // Switch languages
  const handleSwapLang = () => {
    const temp = langIn
    setLangIn(langOut)
    setLangOut(temp)
  }

  // Handle engine change
  const handleEngineChange = (eType: string) => {
    setEngineType(eType)
    const eng = POPULAR_ENGINES.find((item) => item.id === eType)
    if (eng) {
      setModelName(eng.defaultModel)
      if (eType === "Ollama") {
        setBaseUrl("http://localhost:11434")
      } else if (eType === "SiliconFlow") {
        setBaseUrl("https://api.siliconflow.cn/v1")
      } else if (eType === "DeepSeek") {
        setBaseUrl("https://api.deepseek.com")
      } else {
        setBaseUrl("")
      }
    }
  }

  // File upload handler
  const handleFileUpload = async (selectedFile: File) => {
    if (!selectedFile.name.toLowerCase().endsWith(".pdf")) {
      setErrorMsg("请上传 PDF 格式的文件")
      return
    }
    setErrorMsg(null)
    setIsUploading(true)

    const formData = new FormData()
    formData.append("file", selectedFile)

    try {
      const res = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      })
      if (!res.ok) {
        throw new Error("上传失败: " + (await res.text()))
      }
      const data = await res.json()
      setFile({
        fileId: data.file_id,
        filename: data.filename,
        size: data.size,
      })
      // Reset translation result
      setResult(null)
      setProgress(0)
      setStage("")
    } catch (err: any) {
      setErrorMsg(err.message || "上传出错")
    } finally {
      setIsUploading(false)
    }
  }

  // Start translation via SSE
  const handleStartTranslate = async () => {
    if (!file) {
      setErrorMsg("请先上传需要翻译的 PDF 文件")
      return
    }

    setErrorMsg(null)
    setIsTranslating(true)
    setProgress(1)
    setStage("正在初始化翻译任务...")
    setStageDetail("")
    setResult(null)

    // Build engine config
    const engineConfig: Record<string, any> = {}
    if (engineType === "OpenAI") {
      if (apiKey) engineConfig.openai_api_key = apiKey
      if (baseUrl) engineConfig.openai_base_url = baseUrl
      if (modelName) engineConfig.openai_model = modelName
    } else if (engineType === "DeepSeek") {
      if (apiKey) engineConfig.deepseek_api_key = apiKey
      if (baseUrl) engineConfig.deepseek_base_url = baseUrl
      if (modelName) engineConfig.deepseek_model = modelName
    } else if (engineType === "SiliconFlow") {
      if (apiKey) engineConfig.siliconflow_api_key = apiKey
      if (baseUrl) engineConfig.siliconflow_base_url = baseUrl
      if (modelName) engineConfig.siliconflow_model = modelName
    } else if (engineType === "Ollama") {
      if (baseUrl) engineConfig.ollama_host = baseUrl
      if (modelName) engineConfig.ollama_model = modelName
    }

    try {
      const response = await fetch("/api/translate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_id: file.fileId,
          lang_in: langIn,
          lang_out: langOut,
          engine_type: engineType,
          engine_config: engineConfig,
        }),
      })

      if (!response.ok) {
        throw new Error(`启动翻译失败: ${response.statusText}`)
      }

      const reader = response.body?.getReader()
      if (!reader) throw new Error("无法读取流式响应")

      const decoder = new TextDecoder("utf-8")
      let buffer = ""

      while (true) {
        const { value, done } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() || ""

        let currentEvent = ""
        let currentData = ""

        for (const line of lines) {
          if (line.startsWith("event:")) {
            currentEvent = line.replace("event:", "").trim()
          } else if (line.startsWith("data:")) {
            currentData = line.replace("data:", "").trim()

            if (currentData) {
              try {
                const parsed = JSON.parse(currentData)

                if (currentEvent === "session") {
                  setSessionId(parsed.session_id)
                } else if (currentEvent === "progress") {
                  setProgress(Math.round(parsed.progress || 0))
                  setStage(parsed.stage || "正在翻译...")
                  if (parsed.stage_total > 0) {
                    setStageDetail(
                      `Part ${parsed.part_index}/${parsed.total_parts} | 进度: ${parsed.stage_current}/${parsed.stage_total}`
                    )
                  }
                } else if (currentEvent === "finish") {
                  setProgress(100)
                  setStage("翻译已完成！")
                  setResult({
                    monoUrl: parsed.mono_url,
                    dualUrl: parsed.dual_url,
                    glossaryUrl: parsed.glossary_url,
                  })
                  setIsTranslating(false)
                } else if (currentEvent === "error") {
                  setErrorMsg(parsed.error || "翻译过程中发生错误")
                  setIsTranslating(false)
                }
              } catch {
                // Ignore parse errors on ping lines
              }
            }
          }
        }
      }
    } catch (err: any) {
      setErrorMsg(err.message || "请求失败")
      setIsTranslating(false)
    }
  }

  // Cancel translation
  const handleCancelTranslate = async () => {
    if (sessionId) {
      try {
        await fetch(`/api/cancel/${sessionId}`, { method: "POST" })
      } catch (e) {
        console.error(e)
      }
    }
    setIsTranslating(false)
    setStage("翻译已取消")
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col font-sans transition-colors">
      {/* Top Navbar */}
      <header className="border-b bg-card/60 backdrop-blur sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-xl bg-primary text-primary-foreground flex items-center justify-center font-bold text-lg shadow-sm">
            ∑
          </div>
          <div>
            <h1 className="font-heading font-semibold text-base leading-none tracking-tight">
              PDFMathTranslate <span className="text-primary font-bold">Web</span>
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              基于 BabelDOC 的学术与公式保留专业 PDF 翻译系统
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setIsDark(!isDark)}
            className="rounded-full w-8 h-8 p-0"
          >
            {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
          </Button>

          <a
            href="https://github.com/PDFMathTranslate-next/PDFMathTranslate-next"
            target="_blank"
            rel="noreferrer"
          >
            <Button variant="outline" size="sm" className="gap-1.5 rounded-full text-xs">
              <GithubIcon className="w-3.5 h-3.5" />
              <span>上游仓库</span>
              <ExternalLink className="w-3 h-3 text-muted-foreground" />
            </Button>
          </a>
        </div>
      </header>

      {/* Main Workspace */}
      <main className="flex-1 p-6 grid grid-cols-1 lg:grid-cols-12 gap-6 max-w-[1700px] w-full mx-auto">
        {/* Left Column: Controls (5 cols) */}
        <div className="lg:col-span-5 flex flex-col gap-5">
          {/* 1. Upload Card */}
          <Card className="border shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Upload className="w-4 h-4 text-primary" />
                <span>PDF 文档上传</span>
              </CardTitle>
              <CardDescription className="text-xs">
                支持学术论文、书籍、研究报告等复杂数学公式排版 PDF
              </CardDescription>
            </CardHeader>
            <CardContent>
              <input
                type="file"
                ref={fileInputRef}
                accept=".pdf"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) handleFileUpload(f)
                }}
              />

              <div
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragOver(true)
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragOver(false)
                  const f = e.dataTransfer.files?.[0]
                  if (f) handleFileUpload(f)
                }}
                onClick={() => fileInputRef.current?.click()}
                className={`border-2 border-dashed rounded-2xl p-6 text-center cursor-pointer transition-all flex flex-col items-center justify-center gap-2.5 ${
                  dragOver
                    ? "border-primary bg-primary/5"
                    : "border-muted-foreground/20 hover:border-primary/50 hover:bg-muted/30"
                }`}
              >
                {isUploading ? (
                  <div className="flex flex-col items-center gap-2 py-4">
                    <Loader2 className="w-8 h-8 animate-spin text-primary" />
                    <span className="text-xs text-muted-foreground">正在上传解析 PDF...</span>
                  </div>
                ) : file ? (
                  <div className="flex flex-col items-center gap-2 py-2">
                    <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex items-center justify-center">
                      <FileText className="w-5 h-5" />
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium line-clamp-1 max-w-xs">{file.filename}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {(file.size / 1024 / 1024).toFixed(2)} MB • 点击或拖拽更换
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2 py-4">
                    <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center text-muted-foreground">
                      <Upload className="w-5 h-5" />
                    </div>
                    <div>
                      <p className="text-sm font-medium">点击或将 PDF 文件拖拽至此</p>
                      <p className="text-xs text-muted-foreground mt-1">
                        自动保留原始排版、表格、高精度 LaTeX 数学公式
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* 2. Language & Translation Engine Config */}
          <Card className="border shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Settings2 className="w-4 h-4 text-primary" />
                <span>翻译与引擎配置</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Language Selection Row */}
              <div className="flex items-center gap-2">
                <div className="flex-1">
                  <Label className="text-xs text-muted-foreground mb-1 block">源语言</Label>
                  <select
                    value={langIn}
                    onChange={(e) => setLangIn(e.target.value)}
                    className="w-full text-sm border rounded-xl px-3 py-2 bg-background focus:ring-2 focus:ring-primary/20 outline-none"
                  >
                    {LANGUAGES.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </div>

                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleSwapLang}
                  className="rounded-full mt-5 h-9 w-9 p-0 shrink-0"
                  title="互换语言"
                >
                  <ArrowRightLeft className="w-3.5 h-3.5" />
                </Button>

                <div className="flex-1">
                  <Label className="text-xs text-muted-foreground mb-1 block">目标语言</Label>
                  <select
                    value={langOut}
                    onChange={(e) => setLangOut(e.target.value)}
                    className="w-full text-sm border rounded-xl px-3 py-2 bg-background focus:ring-2 focus:ring-primary/20 outline-none"
                  >
                    {LANGUAGES.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Translation Engine Selection */}
              <div>
                <Label className="text-xs text-muted-foreground mb-1 block">翻译引擎</Label>
                <div className="grid grid-cols-2 gap-2">
                  {POPULAR_ENGINES.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => handleEngineChange(item.id)}
                      className={`text-left px-3 py-2 rounded-xl text-xs border transition-all ${
                        engineType === item.id
                          ? "border-primary bg-primary/10 font-semibold text-primary shadow-xs"
                          : "border-muted hover:border-border hover:bg-muted/30 text-muted-foreground"
                      }`}
                    >
                      {item.name}
                    </button>
                  ))}
                </div>
              </div>

              {/* Engine Specific Inputs */}
              <div className="space-y-3 pt-1">
                {POPULAR_ENGINES.find((e) => e.id === engineType)?.needKey && (
                  <div>
                    <Label className="text-xs text-muted-foreground mb-1 block">
                      {engineType} API Key
                    </Label>
                    <Input
                      type="password"
                      placeholder={`请输入您的 ${engineType} API Key`}
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      className="rounded-xl text-xs h-9"
                    />
                  </div>
                )}

                <div className="flex items-center justify-between">
                  <button
                    type="button"
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="text-xs text-primary hover:underline flex items-center gap-1"
                  >
                    <span>{showAdvanced ? "收起高级参数" : "展开高级参数 (Base URL / 模型名称)"}</span>
                  </button>
                </div>

                {showAdvanced && (
                  <div className="space-y-3 p-3 rounded-xl bg-muted/30 border border-muted/50 text-xs">
                    <div>
                      <Label className="text-xs text-muted-foreground mb-1 block">API Base URL (留空使用官方默认)</Label>
                      <Input
                        placeholder="https://api.openai.com/v1"
                        value={baseUrl}
                        onChange={(e) => setBaseUrl(e.target.value)}
                        className="rounded-xl text-xs h-8"
                      />
                    </div>
                    <div>
                      <Label className="text-xs text-muted-foreground mb-1 block">指定模型名称</Label>
                      <Input
                        placeholder="gpt-4o-mini"
                        value={modelName}
                        onChange={(e) => setModelName(e.target.value)}
                        className="rounded-xl text-xs h-8"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Action Buttons */}
              <div className="pt-2 flex items-center gap-3">
                <Button
                  onClick={handleStartTranslate}
                  disabled={!file || isTranslating}
                  className="flex-1 rounded-xl gap-2 font-medium h-10 shadow-sm"
                >
                  {isTranslating ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>正在翻译中...</span>
                    </>
                  ) : (
                    <>
                      <Sparkles className="w-4 h-4" />
                      <span>开始智能翻译</span>
                    </>
                  )}
                </Button>

                {isTranslating && (
                  <Button
                    variant="destructive"
                    onClick={handleCancelTranslate}
                    className="rounded-xl h-10 px-4"
                  >
                    取消
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>

          {/* 3. Progress Card (Visible during translation or on finish) */}
          {(isTranslating || progress > 0) && (
            <Card className="border shadow-sm animate-in fade-in-50 duration-300">
              <CardContent className="pt-5 space-y-3">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium text-foreground flex items-center gap-1.5">
                    {progress === 100 ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                    ) : (
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                    )}
                    <span>{stage || "处理中..."}</span>
                  </span>
                  <span className="font-mono font-semibold text-primary">{progress}%</span>
                </div>

                <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
                  <div
                    className="bg-primary h-full transition-all duration-300 ease-out rounded-full"
                    style={{ width: `${progress}%` }}
                  />
                </div>

                {stageDetail && (
                  <p className="text-[11px] text-muted-foreground font-mono">{stageDetail}</p>
                )}

                {/* Finished Downloads */}
                {result && (
                  <div className="pt-2 border-t flex flex-wrap gap-2">
                    {result.dualUrl && (
                      <a href={result.dualUrl} download target="_blank" rel="noreferrer">
                        <Button size="sm" variant="default" className="rounded-xl gap-1.5 text-xs h-8">
                          <Download className="w-3.5 h-3.5" />
                          <span>下载双语对照版</span>
                        </Button>
                      </a>
                    )}
                    {result.monoUrl && (
                      <a href={result.monoUrl} download target="_blank" rel="noreferrer">
                        <Button size="sm" variant="outline" className="rounded-xl gap-1.5 text-xs h-8">
                          <Download className="w-3.5 h-3.5" />
                          <span>下载单语译文版</span>
                        </Button>
                      </a>
                    )}
                    {result.glossaryUrl && (
                      <a href={result.glossaryUrl} download target="_blank" rel="noreferrer">
                        <Button size="sm" variant="secondary" className="rounded-xl gap-1.5 text-xs h-8">
                          <BookOpen className="w-3.5 h-3.5" />
                          <span>下载术语表</span>
                        </Button>
                      </a>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Error Alert */}
          {errorMsg && (
            <Alert variant="destructive" className="rounded-2xl shadow-sm">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle className="text-xs font-semibold">提示信息</AlertTitle>
              <AlertDescription className="text-xs mt-0.5">{errorMsg}</AlertDescription>
            </Alert>
          )}
        </div>

        {/* Right Column: PDF Preview Workspace (7 cols) */}
        <div className="lg:col-span-7 flex flex-col">
          <Card className="flex-1 flex flex-col border shadow-sm overflow-hidden min-h-[560px]">
            <CardHeader className="py-3 px-5 border-b flex flex-row items-center justify-between">
              <div className="flex items-center gap-2">
                <FileText className="w-4 h-4 text-primary" />
                <span className="text-sm font-medium">文档在线预览</span>
                {result && (
                  <Badge variant="secondary" className="text-[11px] rounded-full">
                    已就绪
                  </Badge>
                )}
              </div>

              {result && (
                <div className="flex items-center gap-1 bg-muted p-1 rounded-xl">
                  <button
                    onClick={() => setPreviewTab("dual")}
                    className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                      previewTab === "dual"
                        ? "bg-background text-foreground shadow-xs"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    双语对照
                  </button>
                  <button
                    onClick={() => setPreviewTab("mono")}
                    className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                      previewTab === "mono"
                        ? "bg-background text-foreground shadow-xs"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    单语译文
                  </button>
                </div>
              )}
            </CardHeader>

            <CardContent className="p-0 flex-1 flex flex-col bg-muted/20 relative">
              {result ? (
                <iframe
                  key={previewTab}
                  src={previewTab === "dual" ? result.dualUrl || "" : result.monoUrl || ""}
                  className="w-full flex-1 border-0 rounded-b-2xl h-[calc(100vh-180px)] min-h-[580px]"
                  title="PDF Preview"
                />
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-muted-foreground min-h-[480px]">
                  <div className="w-16 h-16 rounded-2xl bg-muted/60 flex items-center justify-center mb-3">
                    <BookOpen className="w-8 h-8 text-muted-foreground/50" />
                  </div>
                  <h3 className="text-sm font-medium text-foreground">暂无预览内容</h3>
                  <p className="text-xs text-muted-foreground max-w-sm mt-1">
                    在左侧上传 PDF 并点击“开始智能翻译”，完成后即可在此处全屏对照阅读译文。
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  )
}

export default App
