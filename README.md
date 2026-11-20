# 麻醉次專科文獻週報 Anesthesiology Weekly Digest

自動收錄麻醉 12 個次專科的最新 **期刊論文 / 綜述（含系統性回顧、統合分析）/ 臨床指引**，
每週一（台北時間 08:00）由 GitHub Actions 從 PubMed 擷取並更新網站。

次專科：General · Cardiothoracic · Neuro · Pediatric · Obstetric · Regional · Pain Medicine ·
Critical Care · Transplant · Ambulatory · Trauma · Geriatric

## 功能
- 次專科、文章類型皆有 **全選 / 全不選** 按鈕，勾選狀態會記在瀏覽器
- 關鍵字搜尋（標題、摘要、作者、期刊）、排序、分頁
- 可切換歷史週次（預設保留 26 週）
- 每篇附 PubMed、DOI、PMC 免費全文連結與可展開摘要
- 自動深色模式、手機版面

## 部署步驟（不需手動執行任何更新）
1. 在 GitHub 建立**空的**新 repo（例如 `anesth-digest`，Public）
2. 先設定：Repo → **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**
3. （建議）**Settings → Secrets and variables → Actions** 新增：
   - `NCBI_API_KEY`：到 NCBI 帳號設定免費申請，可提高速率限制
   - `NCBI_EMAIL`：你的聯絡信箱
4. 把本資料夾推上 `main` 分支
   ```bash
   cd anesth-digest
   git init && git add . && git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<你的帳號>/anesth-digest.git
   git push -u origin main
   ```
5. 推上去後 Actions 會**自動**偵測到還沒有資料，抓最近 28 天的文獻並發布網站（約 2–5 分鐘）
6. 網址：`https://<你的帳號>.github.io/anesth-digest/`

之後每週一自動更新；需要時仍可在 Actions → Weekly update → Run workflow 手動補抓。
若第 5 步顯示權限錯誤，到 **Settings → Actions → General → Workflow permissions** 改為 **Read and write permissions** 後，在該次執行按 Re-run。每次更新都會 commit，repo 會保持活躍，GitHub 不會因 60 天無活動停用排程。

## 調整收錄範圍
編輯 `scripts/subspecialties.json`：
- `query`：各次專科 PubMed 檢索式（`{ANES}` 代表麻醉通用詞）
- `pubtype_filter`：收錄/排除的文章類型（預設排除 comment、editorial、letter、case report）
- `language_filter`：預設只收英文，設為 `""` 取消
- `max_per_category`：每類每週上限；`keep_weeks`：保留週數

## 本機預覽
```bash
python scripts/fetch_pubmed.py      # 抓最近 7 天
python -m http.server 8000          # 開 http://localhost:8000
```
（直接雙擊 index.html 無法讀取資料，需透過 http 伺服器。）

分類依期刊與關鍵字檢索式判定，同一篇可能屬於多個次專科。
