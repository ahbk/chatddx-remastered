# vLLM
vLLM is used for running local models within our control. A fake vLLM is under planning.

## vLLM 0.24 assumptions (for the fake vLLM)
1. ChatCompletionResponse.prompt_token_ids is a top-level list[int] | None, set only when request.return_token_ids is true:
  - https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L129
  - https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L1070-L1072

2. 0 < temperature < 1e-2 is logged and raised to 1e-2 (_MAX_TEMP):
  - https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/sampling_params.py#L428-L438

3. Extra fact: greedy is temperature < 1e-5, checked after the clamp, so only an exact 0 is greedy; that matches Skeleton.greedy.

4. Schemas with `$defs` and `$ref` are constrained as written; nothing needs inlining. A `response_format`
   `json_schema` becomes the structured-output schema as is
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L595-L598).
   A named `tool_choice` uses the tool's `parameters` as the root schema, so `#/$defs/…` resolve against it
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/tool_parsers/utils.py#L265-L296).
   The default backend `auto` tries xgrammar and falls back to llguidance on any validation error
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/sampling_params.py#L967-L1005); xgrammar's
   unsupported-feature list doesn't include `$ref`
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/v1/structured_output/backend_xgrammar.py#L225-L269).
   Checked: xgrammar 0.2.1 and 0.2.8 (both ends of the pinned range,
   https://github.com/vllm-project/vllm/blob/v0.24.0/requirements/common.txt#L29) compile the sample's
   `management_plan_v1.json` to the same grammar as its inlined copy, up to rule names. So xgrammar, not the
   fallback, constrains it.
5. A named `tool_choice` is constrained, and parsed into `tool_calls`, only when the server runs with
   `--enable-auto-tool-choice` and `--tool-call-parser`. The tool's schema reaches the grammar only through a tool
   parser's `adjust_request`
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L946-L966,
   https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/parser/abstract_parser.py#L455-L464), and there is
   no tool parser without both flags
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/parser/parser_manager.py#L35-L36). Without them, a
   `tool` contract's request goes out unconstrained.

6. Structured output waits for the end of reasoning only when the server runs with `--reasoning-parser`. Without
  a reasoner the grammar's bitmask applies from the first token
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/v1/structured_output/__init__.py#L305-L323), and the
  reasoner comes only from `--reasoning-parser`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/engine/arg_utils.py#L2257-L2259).

7. A request with `thinking_token_budget` is refused unless reasoning is configured (`--reasoning-parser` and/or
  `--reasoning-config`)
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/v1/engine/input_processor.py#L102-L111).

8. Flag names accept `_` for `-`, up to their first `.`, so `--tool_call_parser` is `--tool-call-parser`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L301-L320). `--config FILE`
  pulls arguments from a YAML file
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L298-L299).

Fake vLLM based on 0.24.0 should pin all of them

## Proposed amendments
