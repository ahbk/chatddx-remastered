from pathlib import Path

import pytest
from pydantic import HttpUrl, JsonValue, ValidationError

from chatddx.factors.bundle import Registry
from chatddx.factors.engine import FileDigest, LocalEngine, ModelArtifact, RemoteEngine
from chatddx.factors.lint import lint
from chatddx.factors.request import (
    Message,
    NativeOutput,
    OutputContract,
    Reasoning,
    Sampling,
    Skeleton,
    Slot,
    TextOutput,
)
from chatddx.factors.test.sample import SHA, local_engine, world
from chatddx.factors.trial import Trial
from chatddx.facts.facts import (
    Facts,
    ModelFacts,
    OutputFacts,
    ReasoningFacts,
    Refusal,
    Refused,
    Writes,
)

SAMPLE = Path(__file__).parent / "sample.toml"
QWEN = "Qwen/Qwen3-8B-AWQ"
GPT_OSS = "openai/gpt-oss-20b"


@pytest.fixture
def facts() -> Facts:
    return Facts.load(SAMPLE)


def test_reasoning_levels_become_chunks(facts: Facts) -> None:
    qwen, gpt_oss = facts.models[QWEN], facts.models[GPT_OSS]
    thinking = Reasoning(chat_template_kwargs={"enable_thinking": True})
    assert qwen.reasoning_chunk("default") == thinking
    assert qwen.reasoning_chunk("xhigh") == thinking
    assert qwen.reasoning_chunk("off") == Reasoning(
        chat_template_kwargs={"enable_thinking": False}
    )
    assert qwen.reasoning_chunk("on", budget=2048) == Reasoning(
        chat_template_kwargs={"enable_thinking": True}, thinking_token_budget=2048
    )
    assert gpt_oss.reasoning_chunk("default") == Reasoning(effort="medium")
    assert gpt_oss.reasoning_chunk("on") == Reasoning(effort="medium")
    with pytest.raises(Refused, match="always reasons"):
        _ = gpt_oss.reasoning_chunk("off")
    with pytest.raises(Refused, match="no thinking budget"):
        _ = gpt_oss.reasoning_chunk("high", budget=512)
    with pytest.raises(Refused, match="nothing is known"):
        _ = ModelFacts().reasoning_chunk("high")
    assert qwen.reasons_by_default() is True
    assert ModelFacts().reasons_by_default() is None


def test_recommended_sampling_follows_the_reasoning_level(facts: Facts) -> None:
    qwen = facts.models[QWEN]
    assert qwen.sampling_chunk("off") == Sampling(
        temperature=0.7, top_p=0.8, top_k=20, presence_penalty=1.5
    )
    assert qwen.sampling_chunk("high", top_k=40) == Sampling(
        temperature=0.6, top_p=0.95, top_k=40, presence_penalty=1.5
    )
    with pytest.raises(Refused, match="always reasons"):
        _ = facts.models[GPT_OSS].sampling_chunk("off")
    unrecommended = ModelFacts(reasoning=ReasoningFacts(default="on", on=Writes()))
    with pytest.raises(Refused, match="no sampling is recommended for reasoning 'on'"):
        _ = unrecommended.sampling_chunk()


def test_facts_are_checked(tmp_path: Path) -> None:
    def load(text: str) -> Facts:
        path = tmp_path / "facts.toml"
        _ = path.write_text(text)
        return Facts.load(path)

    with pytest.raises(ValidationError, match="go round"):
        _ = load('[model.m.reasoning]\nlow = "high"\nhigh = "low"\n')
    with pytest.raises(ValidationError, match="ends in no fact"):
        _ = load('[model.m.reasoning]\nlow = "high"\n')
    with pytest.raises(ValidationError, match="Extra inputs"):
        _ = load('[model.m.reasoning]\nlow = { reasoning_effort = "low" }\n')
    with pytest.raises(ValueError, match="only 'model' tables"):
        _ = load("[llm.m]\n")
    other = tmp_path / "other.toml"
    _ = other.write_text(f'[model."{QWEN}"]\n')
    with pytest.raises(ValueError, match="more than one file"):
        _ = Facts.load(SAMPLE, other)


def test_facts_check_pairs(facts: Facts) -> None:
    reg = Registry()
    ids = world(reg)
    template = local_engine(reg)

    def engine(repo: str, *argv: str) -> str:
        model = reg.add(
            ModelArtifact(
                repo=repo,
                revision="0" * 40,
                files=(FileDigest(path="model.safetensors", sha256=SHA),),
            )
        )
        return reg.add(
            LocalEngine.model_validate(
                {**template.model_dump(), "model": model, "argv": argv}
            )
        )

    user = Message(role="user", content=(Slot(slot="case"),))
    response_format: dict[str, JsonValue] = {
        "type": "json_schema",
        "json_schema": {"name": "output", "schema": {"type": "object"}},
    }

    def skeleton(contract: OutputContract, **body: JsonValue) -> str:
        return reg.add(Skeleton(messages=(user,), body=body, contract=contract))

    def findings(skeleton: str, engine: str) -> dict[str, str]:
        trial = reg.add(
            Trial(skeleton=skeleton, engine=engine, cases=(ids["case"],), seeds=(1,))
        )
        return {f.code: f.level for f in lint(reg, [trial], facts=facts)}

    native = skeleton(NativeOutput(), response_format=response_format)
    qwen = engine(QWEN)
    assert findings(native, qwen) == {"vllm.grammar_before_reasoning": "warning"}
    defaults_changed = engine(
        QWEN, "--default-chat-template-kwargs", '{"enable_thinking": false}'
    )
    assert findings(native, defaults_changed) == {
        "vllm.grammar_before_reasoning": "info"
    }
    trial = reg.add(
        Trial(skeleton=native, engine=qwen, cases=(ids["case"],), seeds=(1,))
    )
    assert {f.level for f in lint(reg, [trial])} == {"info"}

    effort = skeleton(TextOutput(), reasoning_effort="low")
    assert findings(effort, qwen) == {"facts.reasoning_unmatched": "warning"}
    gpt_oss = engine(GPT_OSS, "--reasoning-parser", "openai_gptoss")
    assert findings(effort, gpt_oss) == {}
    assert findings(skeleton(TextOutput(), reasoning_effort="none"), gpt_oss) == {
        "facts.reasoning_unmatched": "warning"
    }
    assert findings(
        skeleton(TextOutput(), reasoning_effort="high", thinking_token_budget=512),
        gpt_oss,
    ) == {"facts.budget_refused": "warning"}
    assert findings(native, gpt_oss) == {"facts.output_note": "info"}

    refusing = Facts(
        models={
            QWEN: ModelFacts(output=OutputFacts(native=Refusal(refused="no grammar")))
        }
    )
    trial = reg.add(
        Trial(skeleton=native, engine=qwen, cases=(ids["case"],), seeds=(1,))
    )
    assert [
        (f.code, f.message)
        for f in lint(reg, [trial], facts=refusing)
        if f.code.startswith("facts.")
    ] == [("facts.output_refused", "no grammar")]

    unknown = engine("someone/unknown")
    assert findings(skeleton(TextOutput()), unknown) == {"facts.missing": "info"}
    remote = reg.add(
        RemoteEngine(base_url=HttpUrl("https://example.org/v1"), model=GPT_OSS)
    )
    assert findings(effort, remote) == {}
