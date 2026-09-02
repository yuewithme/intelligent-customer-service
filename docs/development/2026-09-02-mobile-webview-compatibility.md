# 手机 WebView 布局与缓存兼容修复

## 任务目标

- 避免手机内置浏览器长期使用适配前的旧入口页面。
- 让窄屏设备和上报桌面布局宽度的手机触屏 WebView 使用同一套移动端工作台布局。
- 保持普通电脑端布局不变。

## 实施计划

1. 调整管理端 Nginx 缓存策略：入口 HTML 每次重新验证，带哈希的静态资源长期缓存。
2. 统一 CSS 与工作台 JavaScript 的移动端媒体条件，增加无悬停、精确触控设备兜底。
3. 构建管理端并检查产物中的媒体条件与 Nginx 配置。
4. 推送 GitLab `main`，等待生产流水线完成，再验证公开响应头、生产版本和手机视口页面。

## 完成结果

- 管理端 viewport 增加 `viewport-fit=cover`，保留用户缩放能力并适配刘海屏安全区。
- Nginx 对入口 `index.html` 返回 `no-cache, no-store, must-revalidate`，对带哈希的 `/assets/` 返回一年有效期和 `immutable`。
- 工作台布局、子面板和 JavaScript 统一使用 `max-width: 820px` 或 `hover: none` 且 `pointer: coarse` 的媒体条件。
- 移动端底部导航默认隐藏，只在统一的移动媒体条件中显示，避免宽屏手机 WebView 同时命中旧桌面规则。

## 验证情况

- `pnpm ts:check` 通过。
- `pnpm build:prod` 通过，生产产物包含统一媒体条件和新版 viewport。
- 本机 Docker daemon 未运行，Nginx 容器启动改由 GitLab 生产流水线验证。
- Pipeline `#529` 的预检、部署和验收任务全部通过，生产版本为 `511caf252b0b43d3733442c125a24d4d5d136762`，公开健康检查正常。
- 生产 `/workbench` 返回 `Cache-Control: no-cache, no-store, must-revalidate`；新版入口引用 `assets/index-CXnTdev3.js`，该资源返回 `Cache-Control: public, max-age=31536000, immutable`。
- 真实登录页面在 `390 x 844` 手机视口下显示收起的侧栏、顶部导航按钮和底部三页签。
- 模拟 `980px` 布局宽度但保持 `hover: none`、`pointer: coarse` 的手机 WebView 时，统一媒体条件仍命中，侧栏保持抽屉定位且底部导航显示。
