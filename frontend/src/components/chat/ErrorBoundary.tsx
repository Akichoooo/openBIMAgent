import React from "react"
import { AlertTriangle } from "lucide-react"

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info.componentStack)
    // 容错+审计联动:前端崩溃上报后端审计(失败静默,不阻断降级 UI)
    try {
      fetch("/api/v1/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "frontend_crash",
          detail: {
            message: String(error?.message || error),
            stack: String(info.componentStack || "").slice(0, 300),
          },
          result: "reported",
        }),
      }).catch(() => {})
    } catch {
      /* 静默 */
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-3 p-6 text-center">
          <AlertTriangle className="h-8 w-8 text-amber-500" />
          <div className="text-sm font-medium text-foreground">界面出现异常（已隔离，未影响后端）</div>
          <div className="text-xs text-muted-foreground max-w-md break-words">
            {String(this.state.error?.message || this.state.error)}
          </div>
          <button
            type="button"
            onClick={() => this.setState({ hasError: false, error: null })}
            className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs"
          >
            重试渲染
          </button>
        </div>
      )
    }
    return this.props.children
  }
}