# REA MCP 手冊：不跑 `rea setup`，用 agent-switch 掛上 REA

這份手冊教你把 REA 的 MCP server 和 skill 臨時掛進 agent-switch 開的 session，全程不執行 `rea setup`，不改任何 agent 自己的設定檔。

`rea setup` 像是到每個 agent 的設定檔裡永久登記一個工具；這裡的做法比較像只在這一次 session 有效的臨時通行證，session 結束就沒了。

2026-10-31 實測過：

- 用 agent-switch 啟動 OpenCode，stdio 和 HTTP 兩種掛法都跑過完整流程，確實呼叫到 REA 工具。
- 加分項目：裝好 Ghidra 和 JDK 後，透過 stdio 分析原生執行檔也成功。
- Claude Code、Codex、Pi、dsh 都沒有測過。

## 為什麼不用 `rea setup`

`rea setup` 會把 REA 寫進你選的每一個 agent 的設定，之後每個專案都看得到 REA。這份手冊只寫 agent-switch 自己的 session 設定。

| 做法 | 會寫入的地方 |
|---|---|
| `rea setup`（不使用） | 各 agent 的設定檔，例如 Claude Code 的 `~/.claude.json`（`mcpServers.rea`）和 `~/.claude/skills/reverse-engineer-anything`、Codex 的 `~/.codex/config.toml`（`[mcp_servers.rea]`，另外加上 timeout 設定）；改動前會留 `.rea.backup` |
| 本手冊 | agent-switch 的 session 設定，例如 OpenCode 是 `~/.agent-switch/agents/opencode/`；REA repo 內的 build 產物（已列入 `.gitignore`）；加分項目會再多一個 `~/tools/` |

實測結果：跑完整個流程後，`~/.config/opencode` 前後一致，Claude Code 和 Codex 的設定裡也沒有 `rea`。

## 這套怎麼接起來

REA 照常執行，agent-switch 只負責在 session 裡告訴 agent 怎麼連到它。

```text
agent-switch session（只寫 agent-switch 自己的設定）
│
├─ --mcp-stdio ─► node rea.mjs mcp ──GHIDRA_INSTALL_DIR, JAVA_HOME──► Ghidra 12.1.4 + JDK 21（加分）
├─ --mcp-url ───► http://127.0.0.1:8765/mcp（rea mcp --http，你自己先啟動）
└─ --skill ─────► skills/reverse-engineer-anything
```

stdio 模式由 agent 自己啟動 REA，session 結束時一起關掉。HTTP 模式要你先在另一個終端機啟動 REA，agent 再連過去。skill 是給 agent 看的操作說明，不包含工具本身。

## 名詞

| 名詞 | 意思 |
|---|---|
| REA | 本機的逆向分析工具（npm 套件 `rea-agents`），提供 CLI 和 MCP server |
| `rea setup` | REA 的安裝精靈，會改寫各 agent 的設定檔。本手冊不使用它 |
| stdio 模式 | agent 啟動 `node rea.mjs mcp` 子程序，雙方透過標準輸入輸出溝通 |
| HTTP 模式 | 你自己執行 `rea mcp --http`，agent 連到 `http://127.0.0.1:<port>/mcp` |
| skill | `reverse-engineer-anything` 資料夾，告訴 agent 什麼時候用 REA、怎麼用 |
| provider | REA 背後的分析引擎。原生執行檔的反編譯需要 Ghidra |

## 事前準備

- agent-switch，以及要用的 agent。本手冊實測的是 OpenCode。
- Node.js：REA 要求 `^22.23.4 || ^24.22.1 || >=26.12.1`。REA repo 的 `.npmrc` 設了 `engine-strict=true`，版本不符時 `npm ci` 會直接失敗。

## 1. 準備 Node，build REA

stdio 的掛法要用到 `scripts/rea.mjs` 和 skill 的絕對路徑，所以要從原始碼 build REA。

```sh
nvm install 24                  # 實測裝到 v24.22.2，不會改 nvm 的 default alias
NODE24="$(nvm which 24)"
"$NODE24" --version             # 需要 >= v24.22.1

git clone https://github.com/morluto/rea.git ~/workspace/rea   # 已經有就略過
REA_DIR=~/workspace/rea
cd "$REA_DIR"
PATH="$(dirname "$NODE24"):$PATH" npm ci
PATH="$(dirname "$NODE24"):$PATH" npm run build
```

- build 完會多出 `dist/` 和 `skills/reverse-engineer-anything/`，兩者都在 REA 的 `.gitignore` 裡。
- `npm ci` 可能會警告 esbuild 的 install script 沒有被 `allowScripts` 允許。實測不影響 build。
- 後面的指令都假設 `NODE24` 和 `REA_DIR` 已經設好。開新的終端機時要重新設定。

確認 MCP server 本身正常：

```sh
"$NODE24" "$REA_DIR/scripts/rea.mjs" mcp doctor --json
```

結果應該有 `"healthy":true`。實測回報 233 個工具、6 個 prompt。

## 2.（加分）安裝 Ghidra 和 JDK

沒有這一步，REA 照樣能做 JavaScript／Electron 的靜態分析。裝了之後，REA 還能反編譯原生執行檔（ELF、PE 等），像是列出函式、讀虛擬碼、查 xrefs。

全部放在 `~/tools`，不需要 sudo。實測用的是 REA 文件標示驗證過的組合：Ghidra 12.1.4 加 JDK 21。

```sh
mkdir -p ~/tools/downloads && cd ~/tools/downloads
curl -fLO https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip
curl -fLO https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz
echo "ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db  ghidra_12.1.4_PUBLIC_20260921.zip" | sha256sum -c -
echo "d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c  OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz" | sha256sum -c -
unzip -q ghidra_12.1.4_PUBLIC_20260921.zip -d ~/tools
tar -xzf OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz -C ~/tools
mv ~/tools/ghidra_12.1.4_PUBLIC ~/tools/ghidra
mv ~/tools/jdk-21.0.13+9 ~/tools/jdk
```

資料夾名稱拿掉版號後，之後換版本時指令就不用改。Ghidra 的版本是從 `Ghidra/application.properties` 讀的，跟資料夾名稱無關。

確認 REA 能使用這套 Ghidra：

```sh
GHIDRA_INSTALL_DIR=~/tools/ghidra JAVA_HOME=~/tools/jdk \
  "$NODE24" "$REA_DIR/scripts/rea.mjs" doctor --provider ghidra --json
```

`providerInspections` 裡的 ghidra 應該是 `"available":true`，`ghidra-*` 開頭的檢查也都要通過。

三件事要記得：

- REA 要的是 full JDK。只有 JRE、沒有 `javac` 的 Java 不能用。
- Ghidra 12.1.x 需要 JDK 21 以上，12.2 需要 JDK 25 以上。
- doctor 回報 `environment_healthy: false`，而且 `registration:*` 全部失敗，這是正常的，因為你沒有跑 setup。只要看 provider 的檢查就好。

## 3. 啟動 REA，用 agent-switch 連上

### 3.1 stdio（建議）

stdio 不需要另外開終端機：agent 啟動時會自己執行 REA，session 結束就一起關掉。

```sh
agent-switch opencode --url http://127.0.0.1:19090 \
  --mcp-stdio "rea:$NODE24 $REA_DIR/scripts/rea.mjs mcp" \
  --mcp-env "rea:GHIDRA_INSTALL_DIR=$HOME/tools/ghidra" \
  --mcp-env "rea:JAVA_HOME=$HOME/tools/jdk" \
  --skill "$REA_DIR/skills/reverse-engineer-anything"
```

- 這裡用雙引號，讓你的 shell 先把 `$NODE24`、`$REA_DIR` 換成絕對路徑。`--mcp-stdio` 本身不會展開 `~`。
- 直接指定 Node 執行檔和 `rea.mjs`，不要寫成 `rea mcp`。agent 啟動子程序時，PATH 上的 node 版本可能不夠新。
- 兩個 `--mcp-env` 是加分項目才需要的；沒裝 Ghidra 就拿掉。
- 加上 `--no-launch` 可以只看設定、不真的啟動 agent。它會印出 `OPENCODE_CONFIG=...` 和啟動指令。

### 3.2 HTTP

HTTP 適合用同一個 REA 做很多次查詢：Ghidra 只需要匯入一次，之後每次連線都共用已經分析好的 target。

```sh
# 終端機 A：保持開著；Ghidra 的變數設在這裡（加分項目）
GHIDRA_INSTALL_DIR=~/tools/ghidra JAVA_HOME=~/tools/jdk \
  "$NODE24" "$REA_DIR/scripts/rea.mjs" mcp --http --port 8765

# 終端機 B
agent-switch opencode --url http://127.0.0.1:19090 \
  --mcp-url "rea:http://127.0.0.1:8765/mcp" \
  --skill "$REA_DIR/skills/reverse-engineer-anything"
```

- 端點是 `/mcp`。終端機 A 的 stderr 會印出一行 JSON：`"msg":"MCP transport listening"`，裡面有實際的 URL。
- 停止：在終端機 A 按 Ctrl-C。

三件事要記得：

- 一個 server 一次只服務一個 client，新的連線會接手舊的 session。實測 OpenCode 自己就會開好幾條連線，log 裡會出現好幾次 `MCP HTTP session replaced by a new client` 和 `Session not found`，但結果仍然正確。不要讓兩個 agent 共用同一個 server。
- 沒有 TLS，也沒有認證，所以只綁 `127.0.0.1`。
- REA 的文件寫明，client 註冊和 `rea mcp doctor` 都只描述 stdio，所以它們不會檢查 HTTP 模式。

## 4. 確認可以用

### 4.1 看 agent-switch 寫了什麼

OpenCode 的 session 設定在 `~/.agent-switch/agents/opencode/opencode.json`：

| 掛法 | `mcp.rea` 的內容 |
|---|---|
| stdio | `"type": "local"`，`command` 是 `[node 路徑, rea.mjs 路徑, "mcp"]`，`environment` 是 `--mcp-env` 給的值 |
| HTTP | `"type": "remote"`，`url` 是 `http://127.0.0.1:8765/mcp` |

skill 是用 symlink 掛進來的：`~/.agent-switch/agents/opencode/skills/reverse-engineer-anything` 指向 `$REA_DIR/skills/reverse-engineer-anything`。

### 4.2 在 session 裡請 agent 實際使用

| 要確認的事 | 可以這樣問 | 實測結果 |
|---|---|---|
| MCP 接上了 | 呼叫 rea 的 `inspect_analysis_settings`（不帶參數），引用結果中的 `scope` | `rea-runtime` |
| skill 載入了 | 列出你可以用的 skill | 清單中有 `reverse-engineer-anything` |
| JS 分析可用 | 對某個 JS 專案資料夾呼叫 `analyze_javascript_application`，引用 `evidence_id` | 和 CLI 分析同一個資料夾得到的 `evidence_id` 相同 |
| Ghidra 可用（加分） | 用 `open_binary` 開 `/usr/games/kmines`（`provider_id: "ghidra"`），再呼叫 `binary_overview`，回報 `procedure_count` 和上游 provider | `1262`，Ghidra 12.1.4 |

OpenCode V2 用 code mode：agent 會先用 `search` 找到 `tools.rea.<工具名>`，再透過 `execute` 呼叫。所以 REA 的 233 個工具不會一開始就全部塞進 context。

用 Ghidra 時，`open_binary` 只是登記 target，第一個查詢才會觸發 Ghidra 匯入和分析。實測 kmines（167 KB）大約要 9 秒。

## 5.（選用）不經過 agent，直接用 CLI

```sh
"$NODE24" "$REA_DIR/scripts/rea.mjs" analyze-javascript-application /abs/path/to/app --json
```

這行實測過，不需要 Ghidra。原生執行檔的指令可以參考 REA 文件 `docs/native-investigation.md` 的「Start with a function」，例如 `rea inspect <path> --provider ghidra --json`，這部分本手冊沒有實測。CLI 每執行一次指令，都會重新匯入並分析目標一次。

## 6. 疑難排解

| 症狀 | 原因 | 解法 |
|---|---|---|
| `npm ci` 一開始就因為 engine 版本失敗 | Node 版本不符合，而且 `.npmrc` 設了 `engine-strict=true` | 照第 1 步裝 Node 24（>= 24.22.1） |
| HTTP log 出現很多次 `Session not found` | 同一個 client 開了好幾條連線，新的連線接手了舊 session | 不影響結果；一個 server 只給一個 agent 用 |
| doctor 的 `ghidra-java` 檢查失敗 | 用到的是 JRE，或 JDK 版本太舊 | 把 `JAVA_HOME` 指到第 2 步的 `~/tools/jdk` |
| `pkill -f 'rea.mjs mcp --http'` 把自己的 shell 也殺了 | 那個 shell 的命令列也含有這段字串 | 改用 PID：先 `ss -ltnp \| grep :8765` 找到 PID，再 `kill <pid>` |

## 已知限制

- `rea doctor`、`rea update`、`rea uninstall` 只管 setup 寫入的註冊，看不到 agent-switch 注入的 REA。
- REA 有 233 個工具。不支援延遲載入工具定義的 agent，會把全部工具的 schema 都送給模型。REA 文件提供 `REA_MCP_TOOL_FAMILIES` 可以縮小範圍，用 `--mcp-env 'rea:REA_MCP_TOOL_FAMILIES=<ids>'` 傳入；這部分本手冊沒有實測。

## 實測版本（2026-10-31）

REA 6.8.0（`d48e472fa`）、Node v24.22.2、Ghidra 12.1.4、Temurin JDK 21.0.13+9、OpenCode v2.0.3（`~/.bun/bin/opencode2`）、模型 server llama-server（`127.0.0.1:19090`，`swift-1.5-iq3_s`）。agent-switch 執行檔是 `~/tools/bin/agent-switch`，repo HEAD 是 `b6b35c8`；執行檔沒有版本指令，無法確認兩者是否為同一版。
