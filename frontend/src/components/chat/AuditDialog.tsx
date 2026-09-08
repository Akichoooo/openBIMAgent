import React, { useEffect, useState } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { FileText } from "lucide-react"
import { api } from "@/services/api"

export const AuditDialog: React.FC<{ open: boolean; onOpenChange: (o: boolean) => void }> = ({
  open,
  onOpenChange,
}) => {
  const [items, setItems] = useState<any[]>([])
  useEffect(() => {
    if (open) api.getAudit(120).then((r) => setItems(r.items || [])).catch(() => setItems([]))
  }, [open])
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2">
            <FileText className="h-4 w-4 text-primary" />
            审计日志（安全操作留痕）
          </DialogTitle>
        </DialogHeader>
        <ScrollArea className="max-h-[60vh]">
          {items.length === 0 ? (
            <div className="py-8 text-center text-xs text-muted-foreground">暂无审计记录</div>
          ) : (
            <div className="space-y-1 pr-3">
              {items.map((it, i) => (
                <div key={i} className="flex items-start gap-2 rounded-lg border border-border/50 bg-muted/20 px-2 py-1.5 text-[11px]">
                  <span className="font-mono text-muted-foreground shrink-0">{String(it.ts || "").slice(11, 19)}</span>
                  <span className={`font-mono shrink-0 ${it.result === "blocked" || it.result === "failed" ? "text-rose-500" : "text-emerald-600 dark:text-emerald-400"}`}>{it.action}</span>
                  <span className="text-muted-foreground truncate flex-1" title={JSON.stringify(it.detail)}>{JSON.stringify(it.detail)}</span>
                  <span className="text-muted-foreground/70 shrink-0">{it.actor}</span>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}