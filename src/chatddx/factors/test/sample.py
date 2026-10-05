from datetime import UTC, datetime

from chatddx.factors.base import (
    Code,
    Fingerprint,
)
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import (
    Appendix,
    Case,
    Vignette,
)
from chatddx.factors.engine import (
    FileDigest,
    Hardware,
    LocalEngine,
    ModelArtifact,
    Runtime,
)
from chatddx.factors.request import (
    Instructions,
    Message,
    NativeOutput,
    Output,
    Prompt,
    Reasoning,
    Recipe,
    Sampling,
    Skeleton,
    Slot,
    TextOutput,
    compile_request,
)
from chatddx.factors.scoring import (
    Expectation,
    ExpectationSchema,
    Judge,
    Scorer,
    Scoring,
    View,
)
from chatddx.factors.trial import (
    Canary,
    CanarySet,
    Trial,
)

KEY = b"test-key"
RIG = Code(distribution="chatddx", version="0.0.0+dev", revision="abc123")
NOW = datetime(2026, 9, 29, tzinfo=UTC)
SHA = "0" * 64


def fp(text: str) -> Fingerprint:
    return Fingerprint.of(text.encode())


def local_engine(reg: Registry) -> LocalEngine:
    model = reg.add(
        ModelArtifact(
            repo="google/gemma-3-12b-it",
            revision="deadbeef",
            files=(FileDigest(path="model.safetensors", sha256=SHA),),
        )
    )
    return LocalEngine(
        hardware=Hardware(
            gpu="RTX 5090", compute_capability=(12, 0), vram_mib=32607, driver="575.64"
        ),
        runtime=Runtime(version="0.24.0", closure="/nix/store/xxx-vllm-container"),
        model=model,
        chat_template=SHA,
        argv=("--max-model-len", "8192"),
        env={"VLLM_BATCH_INVARIANT": "1"},
    )


def generation_recipe(
    reg: Registry, instructions: str = "You are an emergency physician."
) -> Recipe:
    return Recipe(
        instructions=reg.add(Instructions(text=instructions)),
        prompt=reg.add(
            Prompt(
                segments=(
                    "Case:\n",
                    Slot(slot="vignette"),
                    Slot(slot="appendices"),
                    "\n\nDifferential?",
                )
            )
        ),
        output=reg.add(
            Output(
                contract=NativeOutput(),
                json_schema={
                    "type": "object",
                    "properties": {"ddx": {"type": "array"}},
                },
                guidance="Answer in JSON.",
            )
        ),
        sampling=reg.add(
            Sampling(
                temperature=0.7, top_p=0.9, max_output_tokens=1024, stop=("</ddx>",)
            )
        ),
        reasoning=reg.add(Reasoning(chat_template_kwargs={"enable_thinking": False})),
    )


def generation_skeleton(reg: Registry) -> Skeleton:
    return compile_request(generation_recipe(reg), reg.get)


def world(reg: Registry) -> dict[str, str]:
    vignette = Vignette(
        source="registry", id="c1", fingerprint=fp("A 54-year-old with chest pain.")
    )
    appendix = reg.add(Appendix(vignette=vignette, text="Troponin 80 ng/L."))
    case = reg.add(Case(vignette=vignette, appendices=(appendix,)))
    engine = reg.add(local_engine(reg))
    skeleton = reg.add(generation_skeleton(reg))
    trial = reg.add(
        Trial(
            skeleton=skeleton,
            engine=engine,
            cases=(case,),
            cleanup=("newlines.lf@1", "strip@1"),
            seeds=(1, 2),
        )
    )
    canaries = reg.add(
        CanarySet(
            canaries=(Canary(messages=({"role": "user", "content": "2+2?"},), seed=0),)
        )
    )
    schema = reg.add(ExpectationSchema(json_schema={"type": "object"}))
    expectation = reg.add(
        Expectation(case=case, expectation_schema=schema, data={"ddx": ["ACS"]})
    )
    judge_skeleton = reg.add(
        Skeleton(
            purpose="judge",
            messages=(
                Message(role="user", content=("Grade: ", Slot(slot="completion"))),
            ),
            body={"temperature": 0},
            contract=TextOutput(),
        )
    )
    judge = reg.add(Judge(skeleton=judge_skeleton, engine=engine, seeds=(7,)))
    scoring = reg.add(
        Scoring(
            scorer=reg.add(
                Scorer(
                    code=RIG,
                    entry_point="chatddx_scoring.match:score",
                    expectation_schema=schema,
                    views=(
                        View(
                            output="/ddx/0",
                            expectation="/ddx",
                            metric="match",
                        ),
                        View(metric="judge", judge=judge),
                    ),
                    resources=("sha256:" + SHA,),
                )
            ),
            expectations=(expectation,),
        )
    )
    return {
        "trial": trial,
        "canaries": canaries,
        "case": case,
        "scoring": scoring,
        "judge": judge,
        "engine": engine,
    }
