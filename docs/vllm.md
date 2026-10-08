# vLLM
vLLM is used for running local models within our control. `chatddx.fake_vllm` is a fake vLLM 0.24, started as
`chatddx fake-vllm [--delay S] [--runaway] MODEL [vllm serve's flags]`. It reads `vllm serve`'s own flags, so it can
stand in for vLLM on the same command line (`src/chatddx/fake_vllm/served.py:Served.of`), and its tests pin the
items below (`src/chatddx/fake_vllm/test/`).

## vLLM 0.24 assumptions (for the fake vLLM)
1. ChatCompletionResponse.prompt_token_ids is a top-level list[int] | None, set only when request.return_token_ids is true:
  - https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L129
  - https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L1070-L1072

  In a stream, `prompt_token_ids` comes in the first chunk only, and each chunk's choice carries its delta's
  `token_ids` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L375-L382,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L510-L521).

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
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/parser/parser_manager.py#L35-L36, built for the request
   checks at https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L235-L241),
   and vLLM 0.24 refuses a request whose `tool_choice` is `auto`, `required` or named
   (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L345-L368).
   All three need both flags, although the message for `required` and named names only `--tool-call-parser`.
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

  Flag names may also be abbreviated. `FlexibleArgumentParser` keeps argparse's default `allow_abbrev`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L128-L134), so an unambiguous
  prefix stands for the whole flag, and for a flag given twice the last one wins: `--served-model x` after
  `--served-model-name <digest>` serves the model as `x`, and `--revis r` sets `--revision`. An ambiguous prefix is
  refused: `--chat-templ` could be `--chat-template` or `--chat-template-content-format`. `LocalEngine.argv` refuses
  prefixes of the flags it may not use (`src/chatddx/factors/engine.py:_abbreviations`).

9. `tool_choice: required` constrains the answer to a JSON array of at least one `{name, parameters}`
  call, any of the tools, and a named choice to the tool's parameters. Either way the tool parser replaces any
  `response_format` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/tool_parsers/abstract_tool_parser.py#L119-L148,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/tool_parsers/utils.py#L247-L298).

10. `tool_choice: auto` adds no grammar (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/tool_parsers/utils.py#L297-L298), so with a
  `response_format` the whole answer is constrained to that schema and the model can't write a tool call. The
  request isn't refused: structured outputs go with `auto` and `required`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L759-L768).

11. `vllm serve` takes one optional bare argument, the model (`model_tag`, `nargs="?"`,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/cli_args.py#L347-L352), which then
  replaces `--model` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/cli/serve.py#L52-L53).
  A `--model` option is moved to the front as that argument
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L251-L283), so any other bare
  argument, including everything after `--`, fails with `unrecognized arguments`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/utils/argparse_utils.py#L429) and the server doesn't
  start. `LocalEngine.argv` refuses bare arguments that can't be a flag's value
  (`src/chatddx/factors/engine.py:_bare_arguments`).

12. A chat-completion request accepts keys beyond the OpenAI API
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L193),
  four of which go around other settings: `chat_template` replaces the server's chat template for the request
  (#L317), `structured_outputs` constrains the output beside `response_format` (#L344), `return_prompt_text` puts
  the templated prompt in the response (#L385), and `prompt_logprobs` returns logprobs for the prompt's tokens
  (#L266). A passthrough chunk may not set them (`src/chatddx/factors/request.py:BYPASS_KEYS`).

13. Errors come as `{"error": {"message", "type", "param", "code"}}`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/engine/protocol.py#L60-L68). A request
  for a model the server doesn't serve gets 404, `NotFoundError`, param `model`, "The model `X` does not exist."; a
  request without a model gets the served one
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/models/serving.py#L53-L62).

14. `GET /v1/models` lists each served name, with `root` the model's path and the server's `max_model_len`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/models/serving.py#L64-L76). With
  `--served-model-name <engine digest>`, `id` is the digest and `root` the repo.

15. Non-streaming responses carry `system_fingerprint`, by default `vllm-<version>-<hash8>` of the server's config,
  computed once at start (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/engine/serving.py#L87-L98,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/utils/fingerprint.py#L48). A stream
  carries it on its last chunk: the finish chunk, or the usage chunk when `include_usage` is on
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L733-L740,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L776).
  `--fingerprint-mode none` leaves it out, and `custom` takes `--fingerprint-value`.

16. A `reasoning_effort` also sets the chat template's `enable_thinking` (true unless it is `none`), when the
  request's own `chat_template_kwargs` don't set it
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/protocol.py#L492-L498).
  For Qwen3, `reasoning_effort: none` turns thinking off.

17. Harmony (gpt-oss) refuses `reasoning_effort: none` with 400
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L554-L555), and any
  effort but `high`, `medium` and `low`
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/parser/harmony_utils.py#L122-L128).

18. A response that calls tools finishes with `tool_calls` under `auto` and `required`, and with `stop` under a named
  `tool_choice` (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L700-L706,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L987-L992).

19. `--enable-auto-tool-choice` without a registered `--tool-call-parser` stops the server at start
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/api_server.py#L501-L507).

20. `include_reasoning: false` leaves the reasoning out of the response, when a parser separates it
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/chat_completion/serving.py#L882-L883).

Fake vLLM based on 0.24.0 should pin all of them

## The fake vLLM
A request goes `server.py` → `chat.py:accept`, which refuses it or picks the served model → `chat.py:respond`, which
decides a `Reply` → `chat.py:completion` or `chat.py:stream`, which shape it. `served.py:Served` is the server's
set-up, read once from the command line. Paths are relative to `src/chatddx/fake_vllm/`.

Each entry is one emulated behaviour, then a line with:
- what it follows: `item N` above, `vLLM, no item` where it copies vLLM without an item to pin it yet, or `fake`
  where vLLM has no counterpart and the behaviour is the fake's own;
- where it is coded;
- the tests that pin it (`src/chatddx/fake_vllm/test/`), or `untested`.

The sections follow the sections of `test/test_fake_vllm.py`; "HTTP" is `test/test_fake_vllm_server.py`. Two tests
cut across them: `test_it_answers_every_sample_skeleton_as_its_contract_asks` renders every sample skeleton for both
sample models, served as the old inventory served them, and checks each answer against its contract and schema;
`test_a_tool_round_goes_from_the_toolset_to_the_answer_tool` runs a tool round through `next_request`.

### Serving
- **Flag spelling.** `_` reads as `-` up to the first `.`, `--flag=value` works, the last of a repeated flag wins,
  and a flag's values run to the next `--` argument. Bare arguments are skipped.
  item 8 · `served.py:_options` · `test_it_reads_vllm_serve_s_flags_as_vllm_does`
- **`--config`.** `--config=FILE` is ignored. `--config FILE` refuses to start, since the fake can't read YAML.
  item 8 · `served.py:_options` · `test_it_reads_vllm_serve_s_flags_as_vllm_does`,
  `test_it_won_t_start_where_vllm_won_t_or_where_it_can_t_follow`
- **Flag values.** A flag read for one value refuses to start with none or several; `--max-model-len` and `--port`
  refuse a non-integer.
  fake · `served.py:_one`, `served.py:_int` · `test_it_won_t_start_where_vllm_won_t_or_where_it_can_t_follow`
- **Served names.** `--served-model-name` takes one or more names, else the model is its own name. The first name is
  the one responses carry.
  items 13, 14 · `served.py:Served.of`, `Served.name` · `test_it_reads_vllm_serve_s_flags_as_vllm_does`,
  `test_it_answers_as_the_model_it_serves_or_not_at_all`
- **Context.** `--max-model-len`, else the model's own from `CONTEXT`, else `DEFAULT_CONTEXT`.
  fake (vLLM reads it from the model's config) · `served.py:CONTEXT`, `Served.of` ·
  `test_it_reads_vllm_serve_s_flags_as_vllm_does`
- **Reasoning set-up.** `--reasoning-parser` and `--reasoning-config` count by being there; their values aren't read.
  items 6, 7 · `Served.reasoning_parser`, `Served.reasoning_config` · see "Reasoning"
- **Tool set-up.** `--enable-auto-tool-choice` and `--tool-call-parser`. Tools are parsed only with both; the first
  without the second won't start. The parser's name isn't read.
  items 5, 19 · `Served.of`, `Served.parses_tools` · `test_it_reads_vllm_serve_s_flags_as_vllm_does`,
  `test_it_won_t_start_where_vllm_won_t_or_where_it_can_t_follow`
- **Default chat-template kwargs.** `--default-chat-template-kwargs` must be a JSON object, or it won't start. Only
  `enable_thinking` is read (see "Reasoning").
  item 16 · `served.py:_kwargs` · `test_the_server_s_template_kwargs_are_the_default_a_request_overrides`,
  `test_it_won_t_start_where_vllm_won_t_or_where_it_can_t_follow`
- **Fingerprint.** `--fingerprint-mode` `full` (the default) and `hash` both give `vllm-0.24.0+fake-<hash8>` of the
  model and the flags; `custom` gives `--fingerprint-value`; `none` gives none; any other mode won't start.
  item 15 · `served.py:_fingerprint` · `test_its_fingerprint_follows_its_setup_and_mode`
- **Model family.** Read from the model's name: `gpt-oss` is `harmony`, `qwen3` is `qwen3`, `mistral` is
  `mistral`, anything else none. It decides who thinks and who passes item 5's check without a tool parser.
  fake · `Served.family` · `test_gpt_oss_always_thinks_and_harmony_takes_three_efforts`,
  `test_a_model_of_no_family_it_knows_doesn_t_think`
- **Address.** `--host` (default `127.0.0.1`) and `--port` (default `12099`).
  fake defaults · `Served.of` · `test_it_reads_vllm_serve_s_flags_as_vllm_does`
- **Other flags.** Read and ignored.
  fake · `served.py:_options` · untested
- **Model picking.** A request names a served name, or gets the first served model when it names none; any other
  name gets item 13's 404.
  item 13 · `chat.py:pick` · `test_it_answers_as_the_model_it_serves_or_not_at_all`

### Reasoning
- **Who thinks.** Harmony always. Qwen3 unless `enable_thinking` is false, from the server's default kwargs, then a
  `reasoning_effort` (true unless `none`), then the request's `chat_template_kwargs`, the last that sets it winning.
  No other family.
  item 16 · `chat.py:thinks` · `test_qwen3_thinks_unless_told_not_to`,
  `test_the_server_s_template_kwargs_are_the_default_a_request_overrides`,
  `test_gpt_oss_always_thinks_and_harmony_takes_three_efforts`, `test_a_model_of_no_family_it_knows_doesn_t_think`
- **No room to think under a grammar.** Without `--reasoning-parser`, a request held to a grammar (a `json_schema`
  or `json_object` `response_format`, or `required` or named with a tool parser) gets no thinking.
  item 6 · `chat.py:respond`, `chat.py:_constrained` · `test_without_a_reasoning_parser_a_grammar_leaves_no_room_to_think`
- **What it thinks.** One sentence naming each message's role and word count, and every field but `messages`,
  `model`, `stream` and `stream_options`.
  fake · `chat.py:thinking`, `chat.py:_field` · `test_it_thinks_about_the_fields_it_was_sent`
- **Harmony's efforts.** `reasoning_effort` `none` and anything but `high`, `medium`, `low` are refused.
  item 17 · `chat.py:check` · `test_gpt_oss_always_thinks_and_harmony_takes_three_efforts`
- **Thinking budget.** Refused without `--reasoning-parser` or `--reasoning-config`; else the thinking is cut to
  `thinking_token_budget` tokens and the answer follows whole.
  item 7 · `chat.py:check`, `chat.py:respond` · `test_a_thinking_budget_cuts_the_thinking_short_where_reasoning_is_set_up`
- **Token limit.** `max_completion_tokens`, else `max_tokens`, spent on the thinking first, then the answer; a cut
  finishes `length`.
  vLLM, no item · `chat.py:respond` · `test_max_tokens_are_spent_on_thinking_first`
- **Where the reasoning goes.** Into `reasoning` with `--reasoning-parser`, or for Harmony with a tool parser.
  Otherwise into the content: `<think>\n…\n</think>\n\n` before the answer for Qwen3, `analysis…assistantfinal`
  for Harmony. The closing marker is left out when the tokens ran out before the answer.
  fake (unchecked, see "Not emulated") · `chat.py:respond` ·
  `test_without_a_reasoning_parser_the_thinking_stays_in_the_content`
- **`include_reasoning: false`.** Separated reasoning is left out.
  item 20 · `chat.py:respond` · `test_it_leaves_the_reasoning_out_when_asked_to`

### Sampling
- **Temperature clamp.** 0 < `temperature` < 0.01 is logged with vLLM's warning and raised to 0.01.
  item 2 · `chat.py:temperature` · `test_only_an_exact_zero_temperature_is_greedy`
- **Greedy.** A temperature below 1e-5 after the clamp, so only an exact 0.
  item 3 · `chat.py:respond` (`GREEDY_TEMP`) · `test_only_an_exact_zero_temperature_is_greedy`
- **The text answer.** `DIAGNOSES`, one per line: in order when greedy, else rotated by `seed`, else rotated at
  random.
  fake · `chat.py:_answer`, `ANSWER` · `test_only_an_exact_zero_temperature_is_greedy`

### Outputs
- **What it answers.** A tool call (see "Tools"), else a document, else the text answer.
  item 9 · `chat.py:respond` · `test_held_or_shown_a_schema_it_answers_with_a_document_that_holds`
- **Held to a schema.** `response_format` `json_schema` gives its schema, `json_object` gives `{"type": "object"}`.
  items 4, 10 · `chat.py:_schema` · `test_held_or_shown_a_schema_it_answers_with_a_document_that_holds`
- **Shown a schema.** Without a schema from `response_format`, the first JSON object with a `type` or `properties`
  key in a system message, as a text-output skeleton shows it.
  fake · `chat.py:_shown_schema`, `chat.py:_shown` · `test_held_or_shown_a_schema_it_answers_with_a_document_that_holds`
- **The document.** An instance of the schema, as JSON indented by 2.
  fake · `chat.py:respond` · `test_held_or_shown_a_schema_it_answers_with_a_document_that_holds`
- **Schema instances.** Local `$ref`s are followed. Then `const`, the first `enum`, the first non-null branch of
  `anyOf`/`oneOf`/`allOf`, the first non-null of a type list. Objects get every property (a schema with
  `properties` and no `type` is an object), arrays `minItems` items (3 without it, at least 1) capped by `maxItems`
  (3 without it), strings `fake <key>` (`fake <key> <n>` in an array), integers and numbers `minimum` (else their
  place in an array, else 1), booleans `false`. Past 12 levels it gives `null`.
  item 4 · `chat.py:instance`, `chat.py:_resolve` · `test_what_it_writes_holds_to_the_sample_s_schemas`,
  `test_what_it_writes_follows_references_unions_and_counts`

### Tools
- **Default `tool_choice`.** `auto` with `tools`, `none` without.
  vLLM, no item · `chat.py:tool_choice` · `test_auto_calls_each_tool_once_then_answers`
- **Request checks.** `tools: []` is refused; so is a `tool_choice` without `tools`, a named choice that names no
  tool offered, and any choice but `none`, `auto`, `required` or named. vLLM's messages and `param`.
  vLLM, no item · `chat.py:validate` · `test_a_tool_choice_must_name_a_tool_it_is_offered`
- **No tool parser.** `auto`, `required` and named are refused with vLLM's messages, except for Harmony and Mistral,
  whose tools go out unconstrained: no call, the text answer. `none` is accepted.
  item 5 · `chat.py:check`, `chat.py:_tool` · `test_without_a_tool_parser_tools_are_refused`,
  `test_without_a_tool_parser_harmony_goes_out_unconstrained` (Mistral untested)
- **Named.** Calls the named tool, over any `response_format`.
  item 9 · `chat.py:_tool` · `test_a_named_tool_is_called_and_stops`
- **`required`.** Calls each tool but the last, in order, skipping those called in earlier assistant messages, then
  the last tool offered, which is the answer tool (`src/chatddx/factors/request.py:compile_request` offers it
  last), over any `response_format`.
  item 9, order fake · `chat.py:_tool`, `chat.py:_called` · `test_required_calls_the_toolset_then_the_answer_tool_offered_last`
- **`auto`.** Calls the first tool not yet called, then gives the text answer; with a `response_format` it calls
  nothing and answers with the document.
  item 10, order fake · `chat.py:_tool` · `test_auto_calls_each_tool_once_then_answers`,
  `test_auto_with_a_response_format_answers_by_the_schema_and_calls_nothing`
- **The call.** One per reply; its arguments are an instance of the tool's `parameters` (see "Outputs"), and its id is
  `chatcmpl-tool-fake-N`, N the calls in earlier assistant messages. The content is empty.
  fake · `chat.py:respond`, `chat.py:_call_id` · `test_a_named_tool_is_called_and_stops`,
  `test_a_tool_round_goes_from_the_toolset_to_the_answer_tool`
- **Finish.** `tool_calls` for a call under `auto` and `required`, `stop` under a named choice.
  item 18 · `chat.py:respond` · `test_a_named_tool_is_called_and_stops`,
  `test_required_calls_the_toolset_then_the_answer_tool_offered_last`, `test_auto_calls_each_tool_once_then_answers`

### Responses
- **Tokens.** A token is a word with the whitespace after it; `<think>` and `</think>` are tokens of their own. The
  prompt's tokens are the messages' text and the `tools` as JSON, split on whitespace.
  fake · `chat.py:_words`, `chat.py:_prompt_words`, `chat.py:_generated` · `test_it_returns_token_ids_only_when_asked`
- **Token ids.** A token's CRC-32 modulo `VOCABULARY`, so they change with the prompt. Given only with
  `return_token_ids: true`: `prompt_token_ids` and the choice's `token_ids`.
  item 1 · `chat.py:_ids`, `chat.py:completion` · `test_it_returns_token_ids_only_when_asked`
- **Usage.** `prompt_tokens`, `completion_tokens` and `total_tokens`, counted in the tokens above.
  fake counts · `chat.py:_usage`, `chat.py:_counted` · `test_it_returns_token_ids_only_when_asked`
- **The completion.** `id` `chatcmpl-<hex>`, `object` `chat.completion`, `created`, `model` the first served name,
  one choice (`message` with `role`, `content`, `reasoning` and `tool_calls` when it calls; `logprobs` null,
  `finish_reason`, `stop_reason` null, `token_ids`), `usage`, `system_fingerprint`, `prompt_token_ids`.
  items 1, 15 · `chat.py:completion` · `test_it_answers_whole_when_asked_not_to_stream`,
  `test_it_returns_token_ids_only_when_asked`
- **The stream.** Server-sent events: an opening chunk with `{"role": "assistant", "content": ""}`, a chunk for each
  reasoning token (`reasoning`), each content token and each runaway newline; for each call, a chunk with its index,
  id, type, name and empty arguments, then a chunk for each token of its arguments; a finish chunk with an empty
  delta; with `include_usage`, a usage chunk with no choices; then `data: [DONE]`.
  vLLM, no item · `chat.py:stream` · `test_it_streams_the_thinking_then_the_answer_then_the_usage`,
  `test_it_streams_a_call_as_its_name_then_its_arguments`,
  `test_it_streams_calls_made_at_once_one_after_another_by_their_index`
- **Streamed token ids.** `prompt_token_ids` on the opening chunk only; `token_ids` on each token's chunk.
  item 1 · `chat.py:stream` · `test_it_streams_the_prompt_s_token_ids_in_its_first_chunk_only`
- **Streamed fingerprint.** On the finish chunk, or on the usage chunk with `include_usage`.
  item 15 · `chat.py:stream` · `test_it_streams_the_thinking_then_the_answer_then_the_usage`
- **Continuous usage.** `continuous_usage_stats`, with `include_usage` only, puts the running usage on every chunk.
  vLLM, no item · `chat.py:stream` · `test_asked_for_it_it_counts_the_usage_on_every_chunk_as_it_goes`
- **Runaway.** With `--runaway`, an answer that would finish `stop` goes on with newlines, a token each, until
  `max_tokens` or the context (`max_model_len` less the prompt) runs out, and finishes `length`. A document loses its
  closing brace first. A call or an answer already cut short doesn't run away. As gpt-oss does on malborg at times.
  fake · `chat.py:respond`, `Reply.runaway` ·
  `test_running_away_it_answers_then_writes_newlines_till_max_tokens_run_out`,
  `test_running_away_with_no_max_tokens_it_writes_till_the_context_runs_out`,
  `test_a_call_or_an_answer_cut_short_doesn_t_run_away`, `test_held_to_a_schema_it_runs_away_before_the_closing_brace`,
  `test_served_it_runs_away_when_told_to`

### HTTP
- **`POST /v1/chat/completions`.** A JSON object, answered by a completion or, with `stream: true`, a
  `text/event-stream`. A body that isn't a JSON object gets 400 `BadRequestError`.
  vLLM, no item · `server.py:_Handler.do_POST`, `server.py:_request` ·
  `test_it_serves_chat_completions_over_http`, `test_it_streams_over_http`,
  `test_it_refuses_as_vllm_does_with_its_status_and_error`
- **Errors.** Item 13's body, with its status.
  item 13 · `chat.py:Failure`, `server.py:_Handler._json` · `test_it_refuses_as_vllm_does_with_its_status_and_error`
- **`GET /v1/models`.** A card for each served name.
  item 14 · `server.py:models` · `test_it_lists_what_it_serves_says_its_version_and_is_healthy`
- **`GET /version`.** `{"version": "0.24.0+fake"}`.
  vLLM, no item · `server.py:got`, `served.py:VERSION` · `test_it_lists_what_it_serves_says_its_version_and_is_healthy`
- **`GET /health`.** 200, no body.
  vLLM, no item · `server.py:got` · `test_it_lists_what_it_serves_says_its_version_and_is_healthy`
- **Anything else.** 404 `{"detail": "Not Found"}`. A query string and a trailing `/` are ignored.
  vLLM, no item · `server.py:got`, `server.py:_Handler.do_POST` · `test_it_serves_nothing_else`
- **Pace.** `--delay S` between streamed tokens (0.03 by default on the command line, none for `server.server`).
  fake · `server.py:_Handler._next_token`, `src/chatddx/cli.py` · `test_a_client_that_hangs_up_is_heard_at_once_and_its_request_aborted`
- **Hang-up.** A client that hangs up mid-stream is heard from the socket while the next token waits, and the stream
  stops, logged as `aborted: the client hung up after N tokens`.
  vLLM, no item · `server.py:_Handler._next_token`, `server.py:_Handler._sent` ·
  `test_a_client_that_hangs_up_is_heard_at_once_and_its_request_aborted`
- **Won't start.** A refused set-up exits with `the fake vLLM won't start: …`.
  item 19 · `server.py:run` · `test_the_command_won_t_start_what_vllm_wouldn_t`
- **Several models.** `server.server` serves several `Served`, picked by name; `chatddx fake-vllm` serves one.
  fake · `server.py:server`, `chat.py:pick` · `test_it_answers_every_sample_skeleton_as_its_contract_asks`

### Not emulated
- Abbreviated flags (item 8) aren't expanded, and bare arguments (item 11) aren't refused: the fake can't tell a flag
  that takes a value from one that doesn't.
- `--config FILE` (item 8) is refused, not read.
- The request keys of item 12 (`chat_template`, `structured_outputs`, `return_prompt_text`, `prompt_logprobs`), `n` >
  1, `stop`, `logprobs`, `echo`, and the sampling settings beyond `temperature` and `seed` are ignored.
- `required` makes one call per reply, where item 9's grammar allows several.
- A prompt longer than the context, or a `max_tokens` past it, isn't refused.
- The document doesn't vary with the seed.
- A document holds to the sample's schemas, not to every schema, as a grammar would make it: `minItems` above 3 is
  met only with a `maxItems`, and `maximum`, `exclusiveMinimum`, `multipleOf` and the string keywords are ignored
  (`chat.py:instance`).
- Unchecked against vLLM: gpt-oss without any parser answers `analysis…assistantfinal…`, taking Harmony's channel
  markers to be special tokens skipped in detokenizing; and Harmony with a tool parser but no reasoning parser still
  separates the reasoning.
- Nothing yet runs the factors' `vllm.*` lints and the fake on the same cases, which would catch the two drifting
  apart.

### Extending
1. If it's vLLM's behaviour, pin it first: add it as the next item under "vLLM 0.24 assumptions", with permalinks into
   `v0.24.0`. Items keep their numbers, since other docs cite them.
2. Code it where its stage is:
   - a `vllm serve` flag: a field on `Served`, read in `Served.of` (`_one` and `_int` for values, `Refused` to stop
     the start);
   - a refusal from the request alone: `chat.py:validate`; one that depends on the set-up: `chat.py:check`;
   - what the model thinks or answers: `chat.py:respond`, with a field on `Reply` when the response must carry it;
   - a model's own behaviour: `Served.family` and the matches on it in `chat.py`;
   - a response field: `chat.py:completion`, and `chat.py:stream` for the same field streamed;
   - an endpoint: `server.py:got` for `GET`, `server.py:_Handler.do_POST` for `POST`;
   - an option of the fake's own: `fake-vllm` in `src/chatddx/cli.py`, through `server.py:run` and `server.py:server`.
3. Test it in the section of `test/test_fake_vllm.py` it belongs to, or in `test/test_fake_vllm_server.py` when it
   needs HTTP.
4. Add its entry to the matching section above, in the same shape, and take it off "Not emulated":
   ```
   - **<behaviour>.** <what the fake does>.
     <item N | vLLM, no item | fake> · `<module>.py:<name>` · `<test>` | untested
   ```

## Proposed amendments
- ADD, as item 21 under "vLLM 0.24 assumptions": "A gpt-oss chat completion starts with Harmony's system message,
  which holds the current date unless `VLLM_SYSTEM_START_DATE` is set, so the same request is a different prompt each
  day (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/parser/harmony_utils.py#L132-L138,
  called with no date by `build_harmony_preamble`, https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/openai/parser/harmony_utils.py#L332-L339,
  for every chat request, https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L557-L563)."
  `src/chatddx/inventory/report.py:_template` reports a gpt-oss engine whose env doesn't set it (`engine.harmony_date`).
- ADD, as item 22 under "vLLM 0.24 assumptions": "A model whose `model_type` is `gpt_oss` is rendered with Harmony and
  never with its chat template, `--chat-template` included
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L234,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/entrypoints/serve/render/serving.py#L377-L400). So a
  gpt-oss engine's `chat_template` pins nothing, and what shapes its prompt is the `openai-harmony` package in the
  runtime closure."
  `src/chatddx/inventory/report.py:_template` reports it (`engine.chat_template_unused`), from o11n's report.
- ADD, as item 23 under "vLLM 0.24 assumptions": "With `HF_HUB_OFFLINE`, a model given as a Hugging Face ID is
  resolved to its snapshot with `snapshot_download(repo_id, revision, local_files_only=True)` and no file patterns
  (https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/engine/arg_utils.py#L759-L766,
  https://github.com/vllm-project/vllm/blob/v0.24.0/vllm/transformers_utils/repo_utils.py#L225-L242); a model given as a directory is read as it is.
  huggingface_hub keeps the commit's file listing from the download (`<repo>/trees/<commit>.json`), and offline it
  refuses a snapshot that lacks any listed file the call's patterns select, with `IncompleteSnapshotError`
  (huggingface_hub `_snapshot_download.py:_raise_if_incomplete_snapshot`). So a snapshot downloaded with
  `--exclude` doesn't start from its ID." o11n passes the snapshot's directory (Kompismoln/o11n 84612b2), and
  `src/chatddx/inventory/report.py:imported` takes it as the host's location for the model.
