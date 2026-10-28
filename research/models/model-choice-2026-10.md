# Mises model choice, October 2026

Researched 2026-10-28. Items marked [V] are vendor-reported; [2] rest on secondary sources only (unconfirmed).

## 1. Best models for CAD code (build123d / CadQuery)
No single authoritative cross-model CAD leaderboard exists. The most relevant one is **CADGenBench** (Hugging Face; accepts build123d or CadQuery output; 49 drawing-to-part samples, validity-gated, scored against private ground truth). BenchLM mirrors it (snapshot 2026-10-25, 25 entries) and excludes it from its own overall score. Several entries are harness + model combos, so tooling counts as much as the model.

| Entry (CADGenBench) | Score |
|---|---|
| build123d-mcp 0.3.85 + Claude Opus 5.5 (max effort) | 86.9% |
| Godela (specialist system) | 84.5% |
| build123d-mcp + Claude Opus 5 (xhigh) | 59.6% |
| GPT-5.6 Sol xhigh + custom chain | 45.1% |
| Grok 4.6 high, build123d baseline | 41.5% |
| Claude Fable 5 (plain) | 37.3% |
| GPT-5.5 / Gemini 3.1 Pro (plain) | 29.7% / 21.1% |
| Open: GLM-4.6V, Qwen3-VL 235B (HF baseline) | 16.4% / 16.3% |

Takeaways:
- Anthropic's Opus-class models with a build123d tool loop lead. The jump from about 37% (plain) to about 87% (MCP loop with measurement and feedback) is the strongest evidence that Mises' measurement-tool architecture matters more than the model choice.
- GPT-5.6 Sol is the clear second proprietary choice. Gemini lags on this benchmark.
- Open models tested here are old and weak. Kimi K3 and Qwen3.6 are not on the board.
- Other benchmarks: Text2CAD-Bench (arXiv 2605.18430, CadQuery, 600 items; finds models degrade on complex topology) and BenchCAD (17.9k CadQuery programs; finds models get coarse shape but not faithful parametric programs). Per-model numbers were not retrieved.

## 2. Kimi
- Latest: **Kimi K3.1 Pro**, API launch 2026-10-23 [2]. Model ids `kimi-k3.1-pro` and `kimi-k3.1-pro-highspeed` (about 2-3x faster) are confirmed in the Kimi API docs.
- K3: released July 2026, 2.8T-parameter MoE, 104B active, 1M context, text + image. Open weights are on HF (`moonshotai/Kimi-K3`) under the "Kimi K3 License" (not stated to be MIT; read it before shipping). K3.1 Pro weights are only "promised by Nov 4" [2].
- 3D/CAD evidence: **none found.** The K3 model card lists no CAD/3D benchmark; it only names CAD and chip design as example tasks. Its coding scores are vendor-reported [V]: Terminal-Bench 2.1 88.3, DeepSWE 67.5, MCPMark 94.5. Artificial Analysis index 55 for K3.1 Pro vs 58 for Opus 5.5 [2]. Treat the "good at 3D" claim as anecdotal until tested in Mises.
- API: OpenAI- and Anthropic-compatible, base URL `https://api.moonshot.ai/v1` (docs moved to platform.kimi.ai), tool calling supported. Thinking is always on and returns `reasoning_content`, which **must be passed back** on multi-turn tool calls. `reasoning_effort` accepts low/high/max (default max). Mises must preserve this field.
- Pricing: $2.00 in / $10.00 out per 1M tokens, Highspeed $4 / $20, cached input $0.20 [2]. The official pricing table did not render, so verify it.
- K2 models stop being offered to new users after 2026-10-31.
- OpenRouter ids (from the provider page): `moonshotai/kimi-k3`, `moonshotai/kimi-k3.1-pro`, `moonshotai/kimi-k2.7-code`, `moonshotai/kimi-k2.6`.
- **Local on 24 GB: no.** 2.8T parameters at MXFP4 is on the order of 1.4+ TB. The earlier Kimi K2.x (1T) is also hundreds of GB. Use it via API only.

## 3. Local models for 24 GB M5 Pro
macOS reserves part of the memory, so budget about 16-18 GB for weights plus KV cache. Sizes are from Ollama library pages; no CAD-specific local benchmarks were found, and tool-reliability notes are anecdotal.

| Model | Ollama tag | Size | Verdict |
|---|---|---|---|
| Qwen3-Coder 30B-A3B (3.3B active) | `qwen3-coder:30b` | 19 GB | Best speed/quality fit. Fast MoE, tools, 256K context. Limit context to about 32K on 24 GB. |
| gpt-oss 20B | `gpt-oss:20b` | 14 GB | Safest memory fit, tools + thinking, leaves headroom for CAD kernel and app. |
| Qwen3.6 27B | `qwen3.6:27b` (MLX: `qwen3.6:27b-mlx`) | 18-19 GB | Likely best raw quality, but tight; dense so slower. Qwen3.6 35B (23-24 GB) is too large. |

In LM Studio, use the equivalent GGUF or MLX builds. Expect local models to land near the 16-20% open-model range on CAD tasks above, so use them for iteration and offline work, not for hard parts.

## 4. OpenRouter free models
- List: `GET https://openrouter.ai/api/v1/models` (bearer key). Optional filters: `supported_parameters=tools` (comma-separated) and `category`.
- Each entry has `id`, `pricing.prompt` / `pricing.completion` (USD per token, strings) and `supported_parameters` (includes `tools`, `tool_choice`, `reasoning`, `structured_outputs`).
- Free test: id ends with `:free` and/or `pricing.prompt == "0" && pricing.completion == "0"`. Filter client-side on `supported_parameters` containing `tools`.
- Limits (official docs): 20 requests/min; 50/day if under 10 credits ever purchased, 1,000/day at 10 or more. HTTP 429 on excess. `GET /api/v1/key` shows `free_model_daily_requests` used/limit/remaining.
- Free models seen on the collection page (2026-10; the list rotates, so always query live) with tool calling stated: `thinkingmachines/inkling:free`, `cohere/north-mini-code:free`, `poolside/laguna-xs-2.1:free`, `xingchen-agi/xing4.0-29b-a4b:free`. Verify via `supported_parameters`.
- Note: one raw fetch of `/models` returned an unexpected Anthropic-style listing, so test the endpoint yourself with curl.

## 5. Local listing and pulling
- Ollama list: `GET http://localhost:11434/api/tags` returns `{models:[{name, model, modified_at, size, digest, details:{format, family, families, parameter_size, quantization_level}}]}`.
- Ollama pull: `POST /api/pull` with `{"model":"qwen3-coder:30b","stream":true}`. NDJSON events carry `status`, `digest`, `total`, `completed`; with `stream:false` you get `{"status":"success"}`.
- LM Studio: `GET http://localhost:1234/v1/models` (OpenAI format). It can list all downloaded models when JIT loading is enabled. The richer native `/api/v0/models` is not documented on the page I fetched.

## 6. Fonts
- The Terminal uses **Bloomberg Prop** (Matthew Carter, 2007, fixed and proportional widths). I found no license terms. It is evidently proprietary and not publicly licensable; do not bundle it. Third-party "lookalikes" such as Bergoom are amateur.
- Mises look (amber/orange on black, dense, narrow): pick one of these.

| Font | License | Why |
|---|---|---|
| IBM Plex Mono | OFL 1.1 | Technical, slightly condensed, institutional feel. https://github.com/IBM/plex |
| Iosevka (Term or Fixed, optionally Extended-Condensed) | OFL 1.1 | Narrow, high density, the closest match to the cramped terminal look. https://github.com/be5invis/Iosevka |
| JetBrains Mono | OFL 1.1 | Tall x-height, very legible at small sizes. https://github.com/JetBrains/JetBrainsMono |

Recommendation: Iosevka for dense data panes, IBM Plex Mono for UI labels.

## Recommendations
| Use | Pick |
|---|---|
| Default agent (quality) | Claude Opus 5.5 with the measurement-tool loop |
| Second proprietary | GPT-5.6 Sol (xhigh) |
| Value/long-run via API | Kimi K3.1 Pro, after testing it on Mises CAD tasks |
| Local | `qwen3-coder:30b`, `gpt-oss:20b`, `qwen3.6:27b` |
| Free tier | OpenRouter `:free` models filtered by `tools` |

## Sources
- https://benchlm.ai/benchmarks/cadgenbenchgeneration
- https://github.com/huggingface/cadgenbench
- https://arxiv.org/abs/2605.18430
- https://huggingface.co/moonshotai/Kimi-K3
- https://huggingface.co/moonshotai
- https://platform.kimi.ai/docs/pricing/chat
- https://platform.kimi.ai/docs/guide/start-using-kimi-api
- https://openrouter.ai/moonshotai
- https://openrouter.ai/docs/api-reference/limits
- https://openrouter.ai/docs/api/api-reference/models/get-models
- https://openrouter.ai/collections/free-models
- https://ollama.com/library/qwen3-coder, /gpt-oss, /qwen3.6
- https://docs.ollama.com/api/tags and /api/pull
- https://lmstudio.ai/docs/developer/openai-compat/models
- Secondary [2]: https://aiweekly.co/alerts/moonshots-kimi-k31-pro-scores-55-on-aa-weights-due-nov-4, https://www.yottalabs.ai/post/kimi-k3-1-pro-specs-price-open-weights-benchmarks
- Fonts: https://github.com/jx22/berg (Bloomberg Prop note), https://gitee.com/mirrors_dchest/bergoom
