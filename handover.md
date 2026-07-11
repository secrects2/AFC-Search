# AFC 價格監控系統 - AI 交接文件 (Handover Document)

這份文件總結了近期（2026年7月）對本專案所進行的所有功能擴充與錯誤修正。請下一位接手的 AI 開發者參考此份清單，以了解系統目前的狀態與最新加入的功能邏輯。

---

## 1. 露天市集 (Ruten) 解析器修正
**相關檔案**：`src/parsers/ruten.py`、`src/parsers/generic.py`

*   **平台識別修正**：修正了 `generic.py` 中的 `detect_platform` 邏輯，確保 `ruten.com.tw` 被正確識別為 `ruten`，不再誤判為 `rakuten` (樂天) 或 `other`。
*   **價格抓取邏輯強化**：
    *   重寫了 `RutenParser.extract()`，確保能正確回傳 `title`, `price`, `seller`, `platform`, `price_source` 等欄位。
    *   加入了針對無效價格字串（如 `$Infinity - $-Infinity`）的過濾機制，避免存入錯誤的價格數據。
    *   透過直接鎖定正確的 CSS Class (例如 `.item-price` 等)，精準定位單一價格，避免抓到網頁中其他不相關的數字。

## 2. 儀表板 UI 優化與手機版適配 (Mobile Optimization)
**相關檔案**：`src/web/templates/results.html`、`src/web/templates/exclusions.html`、`src/web/static/dashboard.css`、`src/web/api.py`

*   **手機版面跑版修正**：加入了 CSS Media Queries (`@media (max-width: 768px)`)，修正了在手機螢幕上表格超出邊界的問題。針對手機版隱藏了較不重要的欄位（例如擷取時間），並限制了標題長度（使用 CSS `text-truncate` 或截斷效果）。
*   **介面中文化與精簡**：
    *   將系統狀態與選單的文字改為繁體中文（例如將 "Suspected Violation" 改為「疑似破價」、「Monitor Results」改為「監測結果」）。
    *   簡化了導覽列 (Navbar) 的文字與間距，提升整體閱讀體驗與操作便利性。

## 3. 全域挑除清單功能 (Global Exclusion List)
**相關檔案**：`src/database.py`、`src/web/api.py`、`src/web/templates/exclusions.html`

為了解決使用者想要過濾特定商品（如：「10粒/盒」、「分裝包」、「試用包」）的需求，新增了全域排除機制。
*   **資料庫結構變更**：在 `SCHEMA_SQL` 中新增了 `global_exclusions` 資料表（欄位包含 `id`, `keyword`, `created_at`）。
*   **CRUD 實作**：在 `database.py` 中實作了 `add_global_exclusion()`, `list_global_exclusions()`, `remove_global_exclusion()`。
*   **溯及既往 (Retroactive Exclusion)**：實作了 `retroactively_exclude_candidates(keyword)`。當使用者新增一個排除關鍵字時，系統會自動在 `product_candidates` 資料表中，將所有標題包含該關鍵字的商品狀態直接改為 `excluded`。
*   **前端呈現過濾**：修改了 `get_snapshots()`，在抓取歷史監測紀錄與「疑似破價」列表時，加入 `WHERE c.status != 'excluded'` 的條件，確保剛被排除的商品會立刻從前端畫面消失。

## 4. 搜尋引擎鏈擴充 (Discovery Search Enhancements)
**相關檔案**：`src/search/search_api.py`、`src/search/shopee_search.py`、`src/search/findprice_api.py`

為了解決 Google 搜尋 (SerpAPI) 無法順利索引蝦皮 (Shopee) 最新商品的問題，擴充了找尋新商品連結的機制。
*   **啟用 FindPrice 搜尋器**：系統中原本潛藏的 `FindPriceProvider` 已被正式啟用，並加入到搜尋鏈 (ChainSearchProvider) 中。由於 FindPrice 會聚合各大台灣電商（MOMO、PChome、蝦皮、Rakuten 等）的資料，這能大幅彌補 SerpAPI 的不足。
*   **新增 Shopee Playwright 直搜**：新增了 `src/search/shopee_search.py`，作為專門針對蝦皮站內的搜尋器（透過 Playwright 無頭瀏覽器搜尋）。
    *   *已知問題交接*：蝦皮目前的防機器人/Cloudflare 阻擋極其嚴格，自動化執行有時會觸發驗證碼導致無法抓取。此模組目前被放置於搜尋鏈的最後一環作為備援。
*   **最新搜尋鏈順序**：`SerpAPI (Google)` ➔ `Brave Search` ➔ `FindPrice` ➔ `ShopeeSearch (Playwright)`。

## 5. 本地排程機制 (Local Task Scheduling)
**相關檔案**：`scripts/setup_all_tasks.ps1`、`scripts/create_windows_task.ps1`、`scripts/remove_all_tasks.ps1`、`scripts/run_price_monitor.ps1`

系統目前採用 Windows 工作排程器 (Windows Task Scheduler) 來進行自動化背景作業，而非依賴 Python 內部的排程套件。
*   **機制說明**：透過 PowerShell 腳本，我們向 Windows 註冊了系統層級的排程任務。這確保了即使儀表板或開發伺服器未被開啟，爬蟲與監測作業也能如期在背景執行。
*   **如何管理排程**：
    *   執行 `scripts/setup_all_tasks.ps1` 可自動建立所有需要的監控排程。
    *   執行 `scripts/remove_all_tasks.ps1` 可將註冊在 Windows 工作排程器中的本專案任務全部清除，方便在開發測試或系統遷移時保持乾淨。
*   **實作細節**：排程主要會去呼叫對應的 PowerShell 或批次檔（如 `run_price_monitor.ps1`），然後啟動虛擬環境 (venv) 執行對應的 Python 服務 (`src.services.daily_monitor` 等)。

---

### 下一步開發建議 (To Next AI)
1. **蝦皮反爬蟲對策**：若專案強烈依賴蝦皮資料，建議可嘗試將 `playwright-stealth` 深度整合，或導入住宅代理 IP (Residential Proxies) 以降低被蝦皮阻擋的機率。
2. **LINE / Email 通知**：目前已經有了「疑似破價」的過濾列表，下一步可考慮實作排程每日發送破價通知報表給使用者。
3. **資料庫效能**：目前 SQLite 足以應付，但隨著 `price_snapshots` 資料量變大，未來可考慮定期清理過舊的 snapshot 或進行分區。
4. **跨平台排程**：目前的排程高度依賴 Windows Task Scheduler (PowerShell)，若未來有將系統部署至 Linux / Docker 的需求，需將排程機制改寫為 `cron` 或採用 Python 的 `Celery` / `APScheduler` 等方案。

