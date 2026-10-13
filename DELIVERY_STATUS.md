# 交付狀態

此版本供客戶自行管理正式Bot的受控試運行，不是安全驗收簽署。
保留 `/chat-erase` 與 `compose.release.yml`，完整安全門檻尚未關閉。

GitHub安裝請使用根README的建置步驟；release/customer內的操作手冊亦包含離線交付包操作。
本儲存庫不提供runtime-image.tar、CHECKSUMS.json、客戶Token或私人資料。
客戶需自行建立應用程式、設定伺服器/頻道、保管Token及準備合法人物資料。
預設Fake Provider只供初始化示範，真實模型需客戶自行配置及驗收。

來源包與本地映像摘要是對應候選歷史證據，不代表從GitHub重新建置會得到相同映像摘要。
GitHub版本由本次commit識別；不得用之前的基線commit聲稱包含本輪整改。

PUBLIC_CODE_UPDATE=CONTROLLED_TRIAL
REAL_DISCORD_ACCEPTANCE=BLOCKED
INDEPENDENT_REVIEW=PENDING
RELEASE_VERDICT=BLOCKED
