# AFC 保健品電商價格監控系統

這是一個 Windows-first 的價格監控 MVP。正式部署方式是由 Windows 工作排程器定期呼叫 `scripts\run_price_monitor.ps1`，每日自動讀取商品主檔、解析手動連結或未來搜尋 API 結果，並產出可稽核報表。

## 專案用途

系統會讀取 `data\AFC商品.csv` 作為建議售價與資料庫商品名稱來源：

- 第 1 欄：建議售價 `suggested_price`
- 第 2 欄：商品名稱 `product_name`

若 CSV 沒有標題列，程式會自動套用上述欄位。建議售價可為 `350.000000`、`1300` 或 `"1,300"`。

正式監控以 AFC 官網商品為主。同步官網後會產生 `data\official_products.csv`，排程只監控其中 `monitor_status = active` 的商品；不在官網或尚未確認的 DB 商品不會被主動訪價。

## 安裝方式

請在專案根目錄執行：

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

OCR 為 fallback 能力；本機未安裝 OCR 引擎時，系統仍可執行，並在報表標記 `ocr_status = disabled`。

## 商品主檔

請將商品資料放在：

```text
data\AFC商品.csv
```

目前專案已匯入使用者提供的 CSV。格式範例：

```csv
350.000000,AFC胺基酸
1300.000000,AFC_綠藻錠狀食品(袋裝)
```

`data\AFC商品.csv` 仍保留為建議售價來源。若官網商品無法自動對應到 CSV 商品，會先進入待審或缺價狀態，不會直接納入排程監控。

## 手動連結模式

沒有搜尋 API key 時，可先用 `data\manual_links.csv` 測試商品頁解析與價格比對：

```csv
product_name,url,platform
AFC胺基酸,https://example.com/product,manual
```

搜尋 API 已預留在 `src\search\search_api.py`，之後可替換為 Google Custom Search API、Bing Search API 或 SerpAPI。

## 手動執行

```powershell
python main.py --products data\AFC商品.csv
```

或指定手動連結：

```powershell
python main.py --products data\AFC商品.csv --manual-links data\manual_links.csv
```

排程模式：

```powershell
python main.py --products data\AFC商品.csv --manual-links data\manual_links.csv --scheduled
```

## Windows 工作排程器正式部署

### 第一步：建立虛擬環境

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 本機網站版

網站是本機管理與稽核介面，正式定期監控仍由 Windows 工作排程器執行。

啟動網站：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\run_dashboard.ps1
```

或：

```powershell
scripts\run_dashboard.bat
```

開啟：

```text
http://127.0.0.1:8001
```

網站功能：

- 查看最近執行摘要
- 查看官網商品目錄統計
- 審核官網商品與 DB 商品對應
- 查看疑似破價清單
- 查詢全部結果
- 下載 `price_monitor_report.xlsx`、`violations.csv`、`all_results.csv`
- 管理 `data\manual_links.csv`
- 手動執行一次價格監控
- 查看 `scheduler.log`、`run.log`、`dashboard_run.log`

## 官網商品目錄、圖片同步與圖片比對

可從 AFC 官網公開 sitemap 與商品頁同步商品目錄與主圖：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\update_official_images.ps1
```

同步後 `data\AFC商品.csv` 會增加欄位：

```text
official_product_url
official_image_url
official_image_path
official_image_hash
official_match_score
official_sync_status
```

同步後也會建立：

```text
data\official_products.csv
```

此檔是正式排程的監控目錄，主要欄位：

```text
official_product_name
official_product_url
official_image_url
official_image_hash
matched_db_product_name
suggested_price
match_score
monitor_status
review_status
decision_source
```

`monitor_status` 狀態：

- `active`：已納入排程監控
- `pending_review`：需管理員在網站「待審商品」確認
- `missing_suggested_price`：官網有商品，但 CSV 尚無建議售價
- `rejected`：管理員確認不納入監控

管理員在網站審核後，系統會寫入：

```text
data\product_review_decisions.csv
```

後續再次同步官網時，人工決策會套回 `official_products.csv`，避免同一商品重複待審。

系統排程執行時，如果商品標題文字比對分數不足，且商品主檔已有 `official_image_hash`，會改用賣場頁圖片與官網主圖做 hash 比對。圖片比對命中時，會在報表中記錄：

```text
official_image_url
image_match_status
image_match_score
```

安全設計：

- 預設只綁定 `127.0.0.1`
- 不提供任意檔案下載
- 不顯示環境變數、API key 或 token
- 網站只寫入 `data\manual_links.csv` 與 `data\product_review_decisions.csv`

### 第二步：手動測試

```powershell
python main.py --products data\AFC商品.csv
```

或：

```powershell
scripts\run_price_monitor.bat
```

或：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\run_price_monitor.ps1
```

### 第三步：建立 Windows 工作排程

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\create_windows_task.ps1
```

預設任務名稱為：

```text
AFC Price Monitor
```

預設每日早上 8:00 執行，使用目前登入的 Windows 使用者，僅在使用者登入時執行。

### 第四步：確認排程是否建立成功

請開啟：

```text
工作排程器 > 工作排程器程式庫 > AFC Price Monitor
```

可手動按「執行」測試。若失敗，先看 `logs\scheduler.log`。

### 第五步：查看執行紀錄

```text
logs\scheduler.log
logs\run.log
```

`scheduler.log` 是 Windows 工作排程器入口紀錄；`run.log` 是 Python 程式內部流程紀錄。

## 報表輸出

每次執行會建立獨立時間戳資料夾：

```text
output\
  20260707_080000\
    all_results.csv
    violations.csv
    price_monitor_report.xlsx
    screenshots\
```

同時會更新：

```text
output\latest\
  all_results.csv
  violations.csv
  price_monitor_report.xlsx
```

Excel 報表包含：

- `疑似破價`
- `全部結果`
- `未抓到價格`
- `可能相關需人工確認`
- `執行摘要`

疑似破價條件：

- 商品名稱相似度 `>= match_threshold`
- 成功取得價格
- 擷取價格 `< suggested_price - price_tolerance`

## 合規注意事項

- 只處理公開可瀏覽頁面。
- 不繞過登入限制、CAPTCHA 或反爬蟲機制。
- 平台阻擋時會標記為 `page_blocked`，不硬爬。
- 每次請求都有 timeout 與 retry 上限。
- 預設以手動連結或搜尋 API 為入口，避免大量爬平台搜尋頁。

## 常見問題

### 沒有搜尋 API key 怎麼測？

使用 `data\manual_links.csv` 放入商品頁 URL，即可先驗證解析、比價與報表流程。

### OCR 沒安裝會不會失敗？

不會。報表會標記 `ocr_status = disabled`，商品頁 DOM 或文字抓不到價格時會標記 `price_not_found`。

### 排程器顯示成功但沒有報表？

請檢查：

- `.venv` 是否存在
- `logs\scheduler.log`
- `logs\run.log`
- `data\AFC商品.csv` 是否存在
- 工作排程的「起始位置」是否為專案根目錄，腳本已自動設定
