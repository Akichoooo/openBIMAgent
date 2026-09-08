import { Toaster as Sonner, type ToasterProps } from "sonner"
import type { CSSProperties } from "react"

/**
 * shadcn 风格 sonner Toaster 封装。
 *
 * 本项目用 document.documentElement 的 `.dark` class 管理主题（见 App.tsx），
 * 未引入 next-themes，故 theme 由调用方依据 isDark 显式传入。
 * 主题色变量是 HSL 分量格式（--popover: 0 0% 100%），需包一层 hsl() 才是合法颜色。
 */
const Toaster = ({ theme = "system", ...props }: ToasterProps) => {
  return (
    <Sonner
      theme={theme}
      className="toaster group"
      richColors
      position="top-center"
      style={
        {
          "--normal-bg": "hsl(var(--popover))",
          "--normal-text": "hsl(var(--popover-foreground))",
          "--normal-border": "hsl(var(--border))",
        } as CSSProperties
      }
      {...props}
    />
  )
}

export { Toaster }
