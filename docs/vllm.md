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
5. Without `--enable-auto-tool-choice` and `--tool-call-parser` there is no tool parser
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/parser/parser_manager.py#L35-L36),
   and vLLM 0.24 refuses a request whose `tool_choice` is `auto` (needs both flags)
   or `required` or named (needs `--tool-call-parser`)
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L345-L368).
   The exceptions are gpt-oss (harmony) and Mistral tokenizers, which pass that check;
   their request then goes out unconstrained, since the tool's schema reaches the grammar only through
   a tool parser's `adjust_request`
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L946-L966,
   https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/parser/abstract_parser.py#L455-L464).

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

  Only the two-token form is expanded: `--config FILE` (an exact match,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L298-L299)
  splices the file's arguments in before the rest of the command line, so the command line wins
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L489-L497).
  `--config=FILE` is accepted by the parser
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/cli_args.py#L368-L372) and then ignored.

9. `tool_choice: required` constrains the answer to a JSON array of at least one `{name, parameters}`
  call, any of the tools, and a named choice to the tool's parameters. Either way the tool parser replaces any
  `response_format` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/tool_parsers/abstract_tool_parser.py#L119-L148,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/tool_parsers/utils.py#L247-L298).

10. `tool_choice: auto` adds no grammar (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/tool_parsers/utils.py#L297-L298), so with a
  `response_format` the whole answer is constrained to that schema and the model can't write a tool call. The
  request isn't refused: structured outputs go with `auto` and `required`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L759-L768).

Fake vLLM based on 0.24.0 should pin all of them

## Proposed amendments

- ADD to item 8: Flag names may also be abbreviated. `FlexibleArgumentParser` keeps argparse's default
  `allow_abbrev` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L128-L134), so an
  unambiguous prefix stands for the whole flag, and for a flag given twice the last one wins: `--served-model x`
  after `--served-model-name <digest>` serves the model as `x`, and `--revis r` sets `--revision`. An ambiguous
  prefix is refused: `--chat-templ` could be `--chat-template` or `--chat-template-content-format`.
  `LocalEngine.argv` refuses prefixes of the flags it may not use (`src/chatddx/factors/engine.py:_abbreviations`).

- ADD as item 11: `vllm serve` takes one optional bare argument, the model (`model_tag`, `nargs="?"`,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/cli_args.py#L347-L352), which then
  replaces `--model` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/cli/serve.py#L52-L53).
  A `--model` option is moved to the front as that argument
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L251-L283), so any other bare
  argument, including everything after `--`, fails with `unrecognized arguments`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L429) and the server doesn't
  start. `LocalEngine.argv` refuses bare arguments that can't be a flag's value
  (`src/chatddx/factors/engine.py:_bare_arguments`).

- ADD as item 12: A chat-completion request accepts keys beyond the OpenAI API
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L193),
  four of which go around other settings: `chat_template` replaces the server's chat template for the request
  (#L317), `structured_outputs` constrains the output beside `response_format` (#L344), `return_prompt_text` puts
  the templated prompt in the response (#L385), and `prompt_logprobs` returns logprobs for the prompt's tokens
  (#L266). A passthrough chunk may not set them (`src/chatddx/factors/request.py:BYPASS_KEYS`).

- CHANGE "A fake vLLM is under planning." → "`chatddx.fake_vllm` is a fake vLLM 0.24, started as
  `chatddx fake-vllm [--delay S] [--runaway] MODEL [vllm serve's flags]`. It reads `vllm serve`'s own flags, so it
  can stand in for vLLM on the same command line (`src/chatddx/fake_vllm/served.py:Served.of`), and its tests pin
  the items below (`src/chatddx/fake_vllm/test/test_fake_vllm.py`)."

- CHANGE item 5, "or `required` or named (needs `--tool-call-parser`)" → "or `required` or named. All three need
  both flags: the tool parser exists only with both
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/parser/parser_manager.py#L35-L36, built for the request checks at
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L235-L241), although the message for `required` and named names only
  `--tool-call-parser`." `vllm.tools_refused` already asks for both (`src/chatddx/factors/lint.py:_runtime`), and
  so does the fake (`src/chatddx/fake_vllm/served.py:Served.parses_tools`).

- ADD as item 13: Errors come as `{"error": {"message", "type", "param", "code"}}`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/engine/protocol.py#L60-L68). A request for a model the server doesn't serve gets 404,
  `NotFoundError`, param `model`, "The model `X` does not exist."; a request without a model gets the served one
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/models/serving.py#L53-L62).

- ADD as item 14: `GET /v1/models` lists each served name, with `root` the model's path and the server's
  `max_model_len` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/models/serving.py#L64-L76). With `--served-model-name <engine digest>`,
  `id` is the digest and `root` the repo.

- ADD as item 15: Non-streaming responses carry `system_fingerprint`, by default `vllm-<version>-<hash8>` of the
  server's config, computed once at start (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/engine/serving.py#L87-L98,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/utils/fingerprint.py#L48). A stream carries it on its last chunk: the finish chunk, or the
  usage chunk when `include_usage` is on (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L733-L740,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L776). `--fingerprint-mode none` leaves it out, and `custom`
  takes `--fingerprint-value`.

- ADD as item 16: A `reasoning_effort` also sets the chat template's `enable_thinking` (true unless it is `none`),
  when the request's own `chat_template_kwargs` don't set it
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L492-L498). For Qwen3, `reasoning_effort: none` turns thinking
  off.

- ADD as item 17: Harmony (gpt-oss) refuses `reasoning_effort: none` with 400
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L554-L555), and any effort but `high`, `medium` and `low`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/parser/harmony_utils.py#L122-L128).

- ADD as item 18: A response that calls tools finishes with `tool_calls` under `auto` and `required`, and with
  `stop` under a named `tool_choice` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L700-L706,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L987-L992).

- ADD as item 19: `--enable-auto-tool-choice` without a registered `--tool-call-parser` stops the server at start
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/api_server.py#L501-L507).

- ADD as item 20: `include_reasoning: false` leaves the reasoning out of the response, when a parser separates it
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L882-L883).

- ADD to item 1: In a stream, `prompt_token_ids` comes in the first chunk only, and each chunk's choice carries its
  delta's `token_ids` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L375-L382,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L510-L521).
