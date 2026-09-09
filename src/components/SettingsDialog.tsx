import { useState, useEffect } from "react"
import {
  Settings2,
  Key,
  BookOpen,
  FileCheck,
  RotateCcw,
  Check,
  ShieldCheck,
} from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Switch } from "@/components/ui/switch"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

import { type AppSettings, DEFAULT_SETTINGS } from "@/types/settings"

interface SettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  settings: AppSettings
  onSave: (newSettings: AppSettings) => void
}

export function SettingsDialog({
  open,
  onOpenChange,
  settings,
  onSave,
}: SettingsDialogProps) {
  const [draft, setDraft] = useState<AppSettings>(settings)
  const [activeTab, setActiveTab] = useState("engines")

  // Sync draft when opened
  useEffect(() => {
    if (open) {
      setDraft(settings)
    }
  }, [open, settings])

  const handleResetDefaults = () => {
    if (confirm("确定要恢复默认设置吗？已保存的 API Key 将被清空。")) {
      setDraft(DEFAULT_SETTINGS)
    }
  }

  const handleSave = () => {
    onSave(draft)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[88vh] flex flex-col p-0 overflow-hidden rounded-3xl border shadow-2xl">
        {/* Header */}
        <DialogHeader className="px-6 pt-5 pb-3 border-b shrink-0 bg-card/60">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-primary/10 text-primary flex items-center justify-center">
              <Settings2 className="w-4 h-4" />
            </div>
            <div>
              <DialogTitle className="text-base font-semibold">系统配置与偏好设置</DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                调整翻译服务密钥、术语提取并发、以及 PDF 双语排版与无水印渲染
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {/* Body Tabs */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
            <TabsList className="w-full grid grid-cols-3 mb-4 rounded-xl p-1 bg-muted/70">
              <TabsTrigger value="engines" className="text-xs gap-1.5 rounded-lg py-1.5">
                <Key className="w-3.5 h-3.5" />
                <span>服务与密钥</span>
              </TabsTrigger>
              <TabsTrigger value="glossary" className="text-xs gap-1.5 rounded-lg py-1.5">
                <BookOpen className="w-3.5 h-3.5" />
                <span>自动术语提取</span>
              </TabsTrigger>
              <TabsTrigger value="layout" className="text-xs gap-1.5 rounded-lg py-1.5">
                <FileCheck className="w-3.5 h-3.5" />
                <span>PDF 排版与输出</span>
              </TabsTrigger>
            </TabsList>

            {/* TAB 1: 服务与密钥 */}
            <TabsContent value="engines" className="space-y-4 focus-visible:outline-none">
              <div className="bg-muted/30 p-3 rounded-2xl border space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <Label className="text-xs font-semibold text-foreground block">
                      默认翻译服务
                    </Label>
                    <span className="text-[11px] text-muted-foreground">
                      页面加载时自动预选的服务引擎
                    </span>
                  </div>
                  <select
                    value={draft.defaultEngine}
                    onChange={(e) =>
                      setDraft({ ...draft, defaultEngine: e.target.value })
                    }
                    className="text-xs border rounded-xl px-2.5 py-1.5 bg-background font-medium focus:ring-2 focus:ring-primary/20 outline-none"
                  >
                    <option value="SiliconFlowFree">SiliconFlow (官方免费体验)</option>
                    <option value="DeepSeek">DeepSeek (官方 API)</option>
                    <option value="SiliconFlow">SiliconFlow (独立 Key)</option>
                    <option value="OpenAI">OpenAI (ChatGPT)</option>
                    <option value="Ollama">Ollama (本地部署)</option>
                  </select>
                </div>

                <div className="flex items-center justify-between pt-1 border-t">
                  <div>
                    <Label className="text-xs font-medium text-foreground block">
                      SiliconFlow 免费服务 JSON 模式
                    </Label>
                    <span className="text-[11px] text-muted-foreground">
                      强制输出结构化 JSON，增强小模型格式稳定性
                    </span>
                  </div>
                  <Switch
                    checked={draft.enableJsonMode}
                    onCheckedChange={(val) =>
                      setDraft({ ...draft, enableJsonMode: val })
                    }
                  />
                </div>
              </div>

              {/* API Keys Configuration */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    各平台 API 凭证与端点 (自动安全保存)
                  </Label>
                  <Badge variant="outline" className="text-[10px] gap-1 font-normal">
                    <ShieldCheck className="w-3 h-3 text-emerald-500" />
                    本地持久存储
                  </Badge>
                </div>

                {/* DeepSeek */}
                <div className="p-3 rounded-xl border bg-card/50 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium">DeepSeek 官方 API</span>
                    <span className="text-[10px] text-muted-foreground font-mono">api.deepseek.com</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <Label className="text-[11px] text-muted-foreground mb-1 block">API Key</Label>
                      <Input
                        type="password"
                        placeholder="sk-..."
                        value={draft.apiKeys.DeepSeek || ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            apiKeys: { ...draft.apiKeys, DeepSeek: e.target.value },
                          })
                        }
                        className="h-8 text-xs rounded-lg"
                      />
                    </div>
                    <div>
                      <Label className="text-[11px] text-muted-foreground mb-1 block">默认模型</Label>
                      <Input
                        placeholder="deepseek-chat"
                        value={draft.modelNames.DeepSeek || ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            modelNames: { ...draft.modelNames, DeepSeek: e.target.value },
                          })
                        }
                        className="h-8 text-xs rounded-lg"
                      />
                    </div>
                  </div>
                </div>

                {/* SiliconFlow */}
                <div className="p-3 rounded-xl border bg-card/50 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium">SiliconFlow (独立个人 Key)</span>
                    <span className="text-[10px] text-muted-foreground font-mono">api.siliconflow.cn</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <Label className="text-[11px] text-muted-foreground mb-1 block">API Key</Label>
                      <Input
                        type="password"
                        placeholder="sk-..."
                        value={draft.apiKeys.SiliconFlow || ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            apiKeys: { ...draft.apiKeys, SiliconFlow: e.target.value },
                          })
                        }
                        className="h-8 text-xs rounded-lg"
                      />
                    </div>
                    <div>
                      <Label className="text-[11px] text-muted-foreground mb-1 block">默认模型</Label>
                      <Input
                        placeholder="Qwen/Qwen2.5-7B-Instruct"
                        value={draft.modelNames.SiliconFlow || ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            modelNames: { ...draft.modelNames, SiliconFlow: e.target.value },
                          })
                        }
                        className="h-8 text-xs rounded-lg"
                      />
                    </div>
                  </div>
                </div>

                {/* OpenAI */}
                <div className="p-3 rounded-xl border bg-card/50 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium">OpenAI / 兼容中转</span>
                    <span className="text-[10px] text-muted-foreground font-mono">api.openai.com</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <Label className="text-[11px] text-muted-foreground mb-1 block">API Key</Label>
                      <Input
                        type="password"
                        placeholder="sk-..."
                        value={draft.apiKeys.OpenAI || ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            apiKeys: { ...draft.apiKeys, OpenAI: e.target.value },
                          })
                        }
                        className="h-8 text-xs rounded-lg"
                      />
                    </div>
                    <div>
                      <Label className="text-[11px] text-muted-foreground mb-1 block">API Base URL</Label>
                      <Input
                        placeholder="https://api.openai.com/v1"
                        value={draft.baseUrls.OpenAI || ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            baseUrls: { ...draft.baseUrls, OpenAI: e.target.value },
                          })
                        }
                        className="h-8 text-xs rounded-lg"
                      />
                    </div>
                  </div>
                </div>

                {/* Global Thread Count */}
                <div className="p-3 rounded-xl border bg-card/50 flex items-center justify-between">
                  <div>
                    <Label className="text-xs font-medium text-foreground block">
                      并发工作线程数 (Translation Threads)
                    </Label>
                    <span className="text-[11px] text-muted-foreground">
                      用于大文档分块和多段落翻译的底层工作线程数 (默认: 4)
                    </span>
                  </div>
                  <Input
                    type="number"
                    min={1}
                    max={16}
                    value={draft.threadCount}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        threadCount: Math.max(1, parseInt(e.target.value) || 1),
                      })
                    }
                    className="w-20 h-8 text-xs text-center font-mono rounded-lg"
                  />
                </div>
              </div>
            </TabsContent>

            {/* TAB 2: 自动术语提取 */}
            <TabsContent value="glossary" className="space-y-4 focus-visible:outline-none">
              <div className="p-3 rounded-2xl border bg-card/50 flex items-center justify-between">
                <div>
                  <Label className="text-xs font-semibold text-foreground block">
                    启用自动学术术语提取
                  </Label>
                  <span className="text-[11px] text-muted-foreground">
                    翻译前自动提取文献专业术语并保持整篇统一 (默认开启，提升专业准确度)
                  </span>
                </div>
                <Switch
                  checked={draft.enableGlossary}
                  onCheckedChange={(val) =>
                    setDraft({ ...draft, enableGlossary: val })
                  }
                />
              </div>

              {draft.enableGlossary && (
                <div className="space-y-3 bg-muted/20 p-3.5 rounded-2xl border border-border/60">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="text-xs font-medium text-foreground block">
                        术语 QPS (每秒查询数速率限制)
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        防止抽取术语时并发过高触发服务商 429 限制 (默认: 4)
                      </span>
                    </div>
                    <Input
                      type="number"
                      min={1}
                      max={30}
                      value={draft.termQps}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          termQps: Math.max(1, parseInt(e.target.value) || 1),
                        })
                      }
                      className="w-20 h-8 text-xs text-center font-mono rounded-lg"
                    />
                  </div>

                  <div className="flex items-center justify-between pt-2 border-t">
                    <div>
                      <Label className="text-xs font-medium text-foreground block">
                        术语池最大工作线程数
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        术语提取线程池容量 (默认: 4)
                      </span>
                    </div>
                    <Input
                      type="number"
                      min={1}
                      max={16}
                      value={draft.termPoolWorkers}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          termPoolWorkers: Math.max(1, parseInt(e.target.value) || 1),
                        })
                      }
                      className="w-20 h-8 text-xs text-center font-mono rounded-lg"
                    />
                  </div>
                </div>
              )}
            </TabsContent>

            {/* TAB 3: PDF 排版与输出 */}
            <TabsContent value="layout" className="space-y-4 focus-visible:outline-none">
              {/* Watermark Mode Selection */}
              <div className="space-y-2">
                <Label className="text-xs font-semibold text-foreground block">
                  PDF 水印模式 (Watermark Mode)
                </Label>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    {
                      id: "no_watermark",
                      title: "无水印 (推荐)",
                      desc: "导出纯净学术版面，无官方水印",
                    },
                    {
                      id: "watermarked",
                      title: "带水印",
                      desc: "保留 BabelDOC 官方水印",
                    },
                    {
                      id: "both",
                      title: "同时生成",
                      desc: "同时输出带水印与无水印",
                    },
                  ].map((wm) => (
                    <button
                      key={wm.id}
                      type="button"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          watermarkMode: wm.id as "no_watermark" | "watermarked" | "both",
                        })
                      }
                      className={`text-left p-2.5 rounded-xl border transition-all ${
                        draft.watermarkMode === wm.id
                          ? "border-primary bg-primary/10 text-foreground ring-1 ring-primary/20"
                          : "border-border hover:bg-muted/40 text-muted-foreground"
                      }`}
                    >
                      <span className="text-xs font-semibold block">{wm.title}</span>
                      <span className="text-[10px] text-muted-foreground leading-snug mt-0.5 block">
                        {wm.desc}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Layout switches */}
              <div className="space-y-2.5 pt-1">
                <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider block">
                  双语与阅读布局控制
                </Label>

                <div className="divide-y border rounded-2xl bg-card/40">
                  <div className="p-3 flex items-center justify-between">
                    <div>
                      <Label className="text-xs font-medium text-foreground block">
                        在双语模式下优先显示翻译后的页面
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        开启后，双语对照中将把中文页面排在原文页面前面
                      </span>
                    </div>
                    <Switch
                      checked={draft.dualTranslateFirst}
                      onCheckedChange={(val) =>
                        setDraft({ ...draft, dualTranslateFirst: val })
                      }
                    />
                  </div>

                  <div className="p-3 flex items-center justify-between">
                    <div>
                      <Label className="text-xs font-medium text-foreground block">
                        使用交替页面进行双页 PDF 阅读
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        适合双页对开模式，左页原文、右页译文对齐
                      </span>
                    </div>
                    <Switch
                      checked={draft.useAlternatingPages}
                      onCheckedChange={(val) =>
                        setDraft({ ...draft, useAlternatingPages: val })
                      }
                    />
                  </div>

                  <div className="p-3 flex items-center justify-between">
                    <div>
                      <Label className="text-xs font-medium text-foreground block">
                        仅在输出中包含已翻译的页面
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        当指定了页码范围（如 1-3）时，输出文件只包含这几页，不包含未翻译页
                      </span>
                    </div>
                    <Switch
                      checked={draft.onlyIncludeTranslatedPage}
                      onCheckedChange={(val) =>
                        setDraft({ ...draft, onlyIncludeTranslatedPage: val })
                      }
                    />
                  </div>
                </div>
              </div>

              {/* Advanced Text Extraction Switches */}
              <div className="space-y-2.5 pt-1">
                <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider block">
                  内容识别与解析
                </Label>

                <div className="divide-y border rounded-2xl bg-card/40">
                  <div className="p-3 flex items-center justify-between">
                    <div>
                      <Label className="text-xs font-medium text-foreground block">
                        翻译表格内文本 (Translate Table Text)
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        智能识别论文表格并在保留单元格结构前提下翻译文本
                      </span>
                    </div>
                    <Switch
                      checked={draft.translateTableText}
                      onCheckedChange={(val) =>
                        setDraft({ ...draft, translateTableText: val })
                      }
                    />
                  </div>

                  <div className="p-3 flex items-center justify-between">
                    <div>
                      <Label className="text-xs font-medium text-foreground block">
                        跳过扫描件检测 (Skip Scanned Detection)
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        遇到“Scanned PDF detected”错误时开启，强制解析排版并忽略扫描告警
                      </span>
                    </div>
                    <Switch
                      checked={draft.skipScannedDetection}
                      onCheckedChange={(val) =>
                        setDraft({ ...draft, skipScannedDetection: val })
                      }
                    />
                  </div>

                  <div className="p-3 flex items-center justify-between border-t">
                    <div>
                      <Label className="text-xs font-medium text-foreground block">
                        自动启用 OCR 变通方案 (Auto OCR Workaround)
                      </Label>
                      <span className="text-[11px] text-muted-foreground">
                        针对重度扫描或含图片底图的文档启用白底覆盖，避免译文与原底图文字混叠
                      </span>
                    </div>
                    <Switch
                      checked={draft.autoEnableOcrWorkaround}
                      onCheckedChange={(val) =>
                        setDraft({ ...draft, autoEnableOcrWorkaround: val })
                      }
                    />
                  </div>
                </div>
              </div>
            </TabsContent>
          </Tabs>
        </div>

        {/* Footer */}
        <DialogFooter className="px-6 py-3 border-t bg-card/60 flex items-center justify-between shrink-0">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleResetDefaults}
            className="text-xs text-muted-foreground hover:text-destructive gap-1.5"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>恢复默认</span>
          </Button>

          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
              className="text-xs rounded-xl"
            >
              取消
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleSave}
              className="text-xs rounded-xl gap-1.5 shadow-sm"
            >
              <Check className="w-3.5 h-3.5" />
              <span>保存配置</span>
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
