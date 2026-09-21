/// <reference types="vite/client" />

// 工作台令牌：后端伺服 dist 时注入 window.__WB_TOKEN（vite dev 下不存在，需优雅降级）
interface Window {
  __WB_TOKEN?: string
}
