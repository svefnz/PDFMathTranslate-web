export interface AppSettings {
  // Service & Keys
  defaultEngine: string
  apiKeys: Record<string, string>
  baseUrls: Record<string, string>
  modelNames: Record<string, string>
  threadCount: number
  enableJsonMode: boolean

  // Terminology & Glossary
  enableGlossary: boolean
  termQps: number
  termPoolWorkers: number

  // PDF Output & Layout
  watermarkMode: "no_watermark" | "watermarked" | "both"
  dualTranslateFirst: boolean
  useAlternatingPages: boolean
  onlyIncludeTranslatedPage: boolean
  noMono: boolean
  noDual: boolean
  translateTableText: boolean
  skipScannedDetection: boolean
  autoEnableOcrWorkaround: boolean
}

export const DEFAULT_SETTINGS: AppSettings = {
  defaultEngine: "SiliconFlowFree",
  apiKeys: {
    OpenAI: "",
    DeepSeek: "",
    SiliconFlow: "",
  },
  baseUrls: {
    OpenAI: "",
    DeepSeek: "https://api.deepseek.com",
    SiliconFlow: "https://api.siliconflow.cn/v1",
    Ollama: "http://localhost:11434",
  },
  modelNames: {
    OpenAI: "gpt-4o-mini",
    DeepSeek: "deepseek-chat",
    SiliconFlow: "Qwen/Qwen2.5-7B-Instruct",
    Ollama: "llama3.2",
    SiliconFlowFree: "Qwen/Qwen2.5-7B-Instruct",
  },
  threadCount: 4,
  enableJsonMode: false,

  enableGlossary: true,
  termQps: 4,
  termPoolWorkers: 4,

  watermarkMode: "no_watermark",
  dualTranslateFirst: false,
  useAlternatingPages: false,
  onlyIncludeTranslatedPage: false,
  noMono: false,
  noDual: false,
  translateTableText: true,
  skipScannedDetection: false,
  autoEnableOcrWorkaround: false,
}

const SETTINGS_STORAGE_KEY = "pdf2zh_web_settings_v1"

export function loadSettings(): AppSettings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY)
    if (!raw) return DEFAULT_SETTINGS
    const parsed = JSON.parse(raw)
    return {
      ...DEFAULT_SETTINGS,
      ...parsed,
      apiKeys: { ...DEFAULT_SETTINGS.apiKeys, ...(parsed.apiKeys || {}) },
      baseUrls: { ...DEFAULT_SETTINGS.baseUrls, ...(parsed.baseUrls || {}) },
      modelNames: { ...DEFAULT_SETTINGS.modelNames, ...(parsed.modelNames || {}) },
    }
  } catch (e) {
    console.error("Failed to load settings from localStorage:", e)
    return DEFAULT_SETTINGS
  }
}

export function saveSettings(settings: AppSettings): void {
  if (typeof window === "undefined") return
  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  } catch (e) {
    console.error("Failed to save settings to localStorage:", e)
  }
}
