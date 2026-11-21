# models/ —— 模型权重放这里（不打进 exe）

三个模型都放在这个文件夹里。**exe 里一个模型都没有**，全部外置，
这样 exe 只有几百 MB（装的是 Python 运行时和库），模型想换就能换。

```
models/
├── gpt-oss-20b-mxfp4.gguf                          ← 语言模型（大脑），约 12.8 GB
├── Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf              ← 视觉模型本体，约 1.9 GB
├── Qwen2.5-VL-3B-Instruct-mmproj-f16.gguf          ← 视觉模型的眼睛（mmproj），约 0.6 GB
└── sd-v1-5/                                        ← 生图模型（diffusers 目录）
    ├── model_index.json
    ├── scheduler/
    ├── text_encoder/
    ├── tokenizer/
    ├── unet/
    └── vae/
```

> 生图模型也可以只放**一个单文件**：`models/dreamshaper_8.safetensors`，
> 然后把 `config.json` 里的 `image.model_path` 改成这个文件。
>
> ⚠️ **文件名以你实际下载到的为准**：不同仓库的文件名会有差异
> （比如 mmproj 可能叫 `mmproj-F16.gguf`、`mmproj-model-f16.gguf`、
> `Qwen2.5-VL-3B-Instruct.mmproj-fp16.gguf`）。
> 对不上就直接改 `config.json` 里的路径，**不用非得改名**。

---

## 一、语言模型：GPT-OSS-20B（4bit GGUF）

| 项目 | 说明 |
|---|---|
| 推荐仓库 | `axteam/gpt-oss-20b-GGUF`（MXFP4 原生 4bit 版，12.8 GB） |
| 备选仓库 | `ggml-org/gpt-oss-20b-GGUF`（llama.cpp 官方） |
| 文件名 | `gpt-oss-20b-GGUF-MXFP4.gguf`（约 12.8 GB） |
| 放到 | `models/gpt-oss-20b-mxfp4.gguf`（或改 config 指向实际文件名） |
| 内存 | 加载后约占 13~14 GB |

小提示：GPT-OSS-20B 原版就是 MXFP4 量化（专家权重本来就是 4bit），
这个 GGUF 就是**完整质量**版，不需要再找 Q4_K_M 重转。
你的 32GB 内存跑它没问题。

## 二、视觉理解：Qwen2.5-VL-3B-Instruct（GGUF + mmproj）

| 项目 | 说明 |
|---|---|
| 推荐仓库 | `ggml-org/Qwen2.5-VL-3B-Instruct-GGUF`（llama.cpp 官方） |
| 备选仓库 | `mradermacher/Qwen2.5-VL-3B-Instruct-GGUF`、`DhruvalLabs/...` |
| 文件 1 | `Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf`（约 1.9 GB） |
| 文件 2 | mmproj 投影文件（约 0.6 GB，**多模态必需，别漏**） |
| 放到 | 两个都放 `models/`，然后改 config 里的 `vision.model_path` / `vision.mmproj_path` |
| 内存 | 加载后约占 3 GB（用完立刻卸载） |


## 三、生图：SD1.5（推荐 DreamShaper 8 单文件）

| 方案 | 说明 |
|---|---|
| **推荐**：单文件 | `Lykon/dreamshaper-8` 里的 `DreamShaper8.safetensors`（约 2 GB），放进 `models/dreamshaper_8.safetensors` |
| 官方目录 | `stable-diffusion-v1-5/stable-diffusion-v1-5` 整个目录放进 `models/sd-v1-5/`（约 4 GB） |
| 更快的 | SD-Turbo（`stabilityai/sd-turbo`），1~4 步出图，把 `image.variant` 改成 `sd15`、`image.steps` 改成 2 |

CPU 上 SD1.5 出 512×512 大概 **40~90 秒**（12 步），SD-Turbo 能压到 **10~20 秒**。
`sdxl-turbo` 也能跑，但 CPU 上偏慢（3~5 分钟一张），不推荐。

---

## 四、下载命令（国内网络，走 hf-mirror 镜像）

**先装下载工具：**

```bat
pip install -U huggingface_hub
set HF_ENDPOINT=https://hf-mirror.com
```

**下载语言模型（12.8 GB，最慢的一步）：**

```bat
huggingface-cli download axteam/gpt-oss-20b-GGUF gpt-oss-20b-GGUF-MXFP4.gguf --local-dir models
```

**下载视觉模型（两个文件，用通配符一次拿全）：**

```bat
huggingface-cli download ggml-org/Qwen2.5-VL-3B-Instruct-GGUF ^
    --include "*Q4_K_M*" "*mmproj*" --local-dir models
```

**下载生图模型（单文件）：**

```bat
huggingface-cli download Lykon/dreamshaper-8 DreamShaper8.safetensors --local-dir models
```

> 如果 `huggingface-cli` 提示命令不存在，新版叫 `hf`：
> `hf download ... --local-dir models`。
>
> 也可以直接双击项目根目录的 `下载模型.bat`，它会自动装工具 + 走镜像站下载。

---

## 五、C 盘放不下怎么办

这台机器 C 盘比较紧。模型可以放到 D 盘，然后在 `config.json` 里写**绝对路径**：

```json
{
  "llm":   { "model_path": "D:/models/gpt-oss-20b-mxfp4.gguf" },
  "vision":{ "model_path": "D:/models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf",
             "mmproj_path": "D:/models/Qwen2.5-VL-3B-Instruct-mmproj-f16.gguf" },
  "image": { "model_path": "D:/models/dreamshaper_8.safetensors" }
}
```

`config.json` 第一次运行程序时会自动生成在 exe 旁边，直接改它就行。

---

## 六、内存账本（为什么必须一个模型一个模型地换）

| 状态 | 内存占用 |
|---|---|
| 只开语言模型（平时） | 约 13 GB |
| 换到视觉模型（看图时） | 约 3 GB（语言模型已卸载） |
| 换到生图模型（出图时） | 约 4 GB（语言模型已卸载） |
| 三个同时开 | **约 20 GB，这在本机是不允许的** |

所以 `loader.py` 做了严格的单槽互斥：任何时刻内存里只有一个模型。
看图、出图结束后会自动把语言模型请回来继续当大脑。
