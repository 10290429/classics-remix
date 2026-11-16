# AGENTS.md

本專案採用 LLM Wiki 模式，完整規則寫在 [`CLAUDE.md`](./CLAUDE.md)。不論你是 Claude、Codex、Gemini 或其他 AI 工具，開始工作前請先讀完該文件，再讀 `wiki/index.md` 與 `wiki/log.md` 最後五筆紀錄。

最重要的四件事：

1. 三層架構：`raw/` 原始資料不可修改，`wiki/` 由你維護，`CLAUDE.md` 是規則。
2. 三種操作：Ingest（收錄）、Query（回答並歸檔）、Lint（健檢），流程見 CLAUDE.md 第四節。
3. 每一筆改編與即將推出資訊都必須附上可開啟的來源網址，查不到就不收。
4. 內容只改 `wiki/`，再執行 `python3 scripts/build.py` 產生網頁，看到 `✓ 全部通過` 才算完成。
