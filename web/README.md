# Web 前端（React + TypeScript）

新版界面的源码在这里。构建产物 `dist/` 已提交到仓库，并由 FastAPI 后端在同一端口托管：只要启动 API，浏览器打开 API 地址（例如 `http://127.0.0.1:8000/`）就能使用，**运行时不需要 Node.js**。设计规范见 [docs/web-redesign-spec.md](../docs/web-redesign-spec.md)。

## 修改并重新构建

需要 Node.js 18 或更高版本。

```powershell
cd web
npm ci
npm run typecheck
npm run build      # 输出到 web/dist，提交时一并提交 dist
npm run watch      # 开发时监听源码，保存后自动重建；刷新浏览器即可看到效果
```

构建只用 esbuild，没有使用 Vite 等较重的工具链。

## 端到端检查

`e2e/smoke.mjs` 用 playwright-core 走一遍完整流程：学生登录、问诊、开单、病历、鉴别、诊断、处置、封存、复盘，再由教师查看看板、做评分复核和病例管理，并截图。运行前需先启动 API（local 账号模式），并准备学生账号和教师账号各一个：

```powershell
$env:BASE='http://127.0.0.1:8000'; $env:OUT='shots'; $env:CHROME='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$env:STUDENT_PW='…'; $env:TEACHER_PW='…'
node e2e/smoke.mjs
```

脚本默认使用的账号名是 `student` 和 `teacher`。最近一次运行的截图和结果见 `docs/evidence/web-redesign-20261113/`。

## 目录

| 路径 | 内容 |
|---|---|
| `src/api.ts` | 接口调用。写操作统一带 `request_id`，重试时沿用同一个请求号，保证幂等 |
| `src/ui.tsx` | 通用组件：图标、提示、面板、标签页、事件选择器、消息提示 |
| `src/pages/` | 登录、病例库、我的训练、训练工作台、复盘 |
| `src/pages/teacher/` | 班级看板、病例管理、反馈复核、评分复核 |
| `src/styles.css` | 设计令牌与全部样式 |
