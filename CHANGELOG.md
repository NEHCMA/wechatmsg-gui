# 更新日志

本仓库在 [Mountain-AI/WeChatMsg](https://github.com/Mountain-AI/WeChatMsg) 基础上继续维护，主要跟随微信客户端版本做适配。

## [Unreleased]

### 新增
- `wxManager/decrypt/key_derive.py`：微信 4.x 图片 `.dat` 的账号级密钥**运行时推导**
  - 从 `.dat` 路径解析 wxid
  - 扫描 `%APPDATA%\Tencent\xwechat\**\kvcomm\` 收集候选 code
  - 逐候选试解 AES 段并校验图片魔数
  - 结果按 wxid 缓存，失效自动重推

### 修复
- **微信 4.2 导出图片全部为空**：4.2 起图片改为账号级密钥，上游内置的 4.0.3 固定密钥解不出有效图片，且失败时无任何报错，表现为 `image/` 目录空。现在 `AES_KEY_MAP` 只保留公开的 V1 密钥，V2 一律走运行时推导；XOR 密钥同理（不再依赖固定 `0x7D` 或外部 `info.json`）
- 移除对 `decrypted_output/db_storage/info.json` 中 `xor_key` 的隐式依赖，降级路径仍在但不再是必需项

### 文档
- `readme.md` 增补「微信 4.2 图片解密修复」章节：加密结构、密钥派生公式、推导流程与已知限制
- `.gitignore` 补齐运行产物规则（`decrypted_output/`、`exports/`、`extracted*/` 等），避免误提交聊天明文

## 适配历史

| 微信版本 | 关键变化 | 处理 |
|---|---|---|
| 4.0 | 数据库 sqlcipher 加密、图片固定密钥 | 上游已支持 |
| 4.1 | 内存口令改 XOR 混淆；hardlink 表改名 `*_v4`；数据库移入子目录 | 从 `Weixin.dll` 静态提取内部 XOR key 还原口令；运行时查 `sqlite_master` 自动识别表名；递归查找数据库目录 |
| 4.2 | 图片密钥由固定改为**账号级**（`MD5(code+wxid)[:16]`） | 新增 `key_derive.py` 运行时推导，见 readme「微信 4.2 图片解密修复」 |

## 已知限制

- `wxgf` 私有格式（微信动图容器）无法解密，导出为 `.bin`，约占图片总数 1%
- 超过 1 MB 且缓存写入不完整的图片会解出损坏文件，需在微信中重新打开该图触发缓存
- 密钥推导依赖本机 `%APPDATA%` 下的 kvcomm 目录，必须在同一台机器、同一 Windows 用户下运行
