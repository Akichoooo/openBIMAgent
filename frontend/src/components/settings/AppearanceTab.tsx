import React, { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Check, Moon, Sun, Laptop } from "lucide-react"

export function AppearanceTab() {
  const [theme, setTheme] = useState<"dark" | "light" | "system">(() => {
    return (localStorage.getItem("wb_theme") as any) || "dark"
  })
  const [fontSize, setFontSize] = useState(() => {
    return localStorage.getItem("wb_font_size") || "13px"
  })

  useEffect(() => {
    localStorage.setItem("wb_theme", theme)
    const root = document.documentElement
    if (theme === "dark") {
      root.classList.add("dark")
    } else if (theme === "light") {
      root.classList.remove("dark")
    } else {
      const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches
      if (isDark) root.classList.add("dark")
      else root.classList.remove("dark")
    }
  }, [theme])

  useEffect(() => {
    localStorage.setItem("wb_font_size", fontSize)
    document.body.style.fontSize = fontSize
  }, [fontSize])

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>外观主题</CardTitle>
          <CardDescription>选择适合您环境的视觉风格，支持无缝深浅色切换</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-4">
            {/* Dark Theme */}
            <div
              onClick={() => setTheme("dark")}
              className={`group relative cursor-pointer rounded-xl border p-4 transition-all hover:border-primary ${
                theme === "dark" ? "border-primary ring-2 ring-primary/20 bg-accent/40" : "border-border/60 bg-card"
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2 font-medium text-sm">
                  <Moon className="h-4 w-4 text-primary" />
                  <span>暗黑极客</span>
                </div>
                {theme === "dark" && <Check className="h-4 w-4 text-primary" />}
              </div>
              <div className="h-20 w-full rounded-lg bg-[#0d1117] p-2.5 border border-[#21262d] flex flex-col gap-1.5 shadow-inner">
                <div className="h-2.5 w-1/3 rounded bg-[#30363d]" />
                <div className="h-2 w-2/3 rounded bg-[#21262d]" />
                <div className="mt-auto flex gap-1.5">
                  <div className="h-4 w-12 rounded bg-primary/70" />
                  <div className="h-4 w-8 rounded bg-[#30363d]" />
                </div>
              </div>
              <p className="mt-2.5 text-xs text-muted-foreground">现代工程 IDE 纯黑质感，完美沉浸 3D 视口</p>
            </div>

            {/* Light Theme */}
            <div
              onClick={() => setTheme("light")}
              className={`group relative cursor-pointer rounded-xl border p-4 transition-all hover:border-primary ${
                theme === "light" ? "border-primary ring-2 ring-primary/20 bg-accent/40" : "border-border/60 bg-card"
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2 font-medium text-sm">
                  <Sun className="h-4 w-4 text-amber-500" />
                  <span>明亮清晰</span>
                </div>
                {theme === "light" && <Check className="h-4 w-4 text-primary" />}
              </div>
              <div className="h-20 w-full rounded-lg bg-[#ffffff] p-2.5 border border-[#e2e8f0] flex flex-col gap-1.5 shadow-inner">
                <div className="h-2.5 w-1/3 rounded bg-[#cbd5e1]" />
                <div className="h-2 w-2/3 rounded bg-[#e2e8f0]" />
                <div className="mt-auto flex gap-1.5">
                  <div className="h-4 w-12 rounded bg-primary/80" />
                  <div className="h-4 w-8 rounded bg-[#e2e8f0]" />
                </div>
              </div>
              <p className="mt-2.5 text-xs text-muted-foreground">高采光环境友好，白底灰阶高对比度</p>
            </div>

            {/* System */}
            <div
              onClick={() => setTheme("system")}
              className={`group relative cursor-pointer rounded-xl border p-4 transition-all hover:border-primary ${
                theme === "system" ? "border-primary ring-2 ring-primary/20 bg-accent/40" : "border-border/60 bg-card"
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2 font-medium text-sm">
                  <Laptop className="h-4 w-4 text-muted-foreground" />
                  <span>跟随系统</span>
                </div>
                {theme === "system" && <Check className="h-4 w-4 text-primary" />}
              </div>
              <div className="h-20 w-full rounded-lg bg-gradient-to-r from-[#ffffff] via-[#94a3b8] to-[#0d1117] p-2.5 border border-border flex items-center justify-center shadow-inner">
                <span className="text-xs font-semibold px-2 py-0.5 rounded bg-background/80 backdrop-blur shadow-sm">Auto</span>
              </div>
              <p className="mt-2.5 text-xs text-muted-foreground">随操作系统外观自动切换昼夜模式</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>文字字号</CardTitle>
          <CardDescription>调整工作台全局基准排版字号</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-4 gap-3">
            {["12px", "13px", "14px", "15px"].map(sz => (
              <button
                key={sz}
                onClick={() => setFontSize(sz)}
                className={`flex items-center justify-center gap-2 rounded-lg border py-2.5 text-xs font-medium transition-all ${
                  fontSize === sz
                    ? "border-primary bg-primary/10 text-primary font-semibold"
                    : "border-border/60 hover:bg-muted text-muted-foreground hover:text-foreground"
                }`}
              >
                <span>{sz}</span>
                {sz === "13px" && <span className="text-[10px] text-muted-foreground">(推荐)</span>}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
