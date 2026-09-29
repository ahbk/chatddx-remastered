import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import chatddx.core.manifest as m

KEY = b"test-hmac-key"
SHA = "a" * 64


@pytest.fixture
def engine_parts():
    artifact = m.ModelArtifact(
        repo="google/gemma-3-4b-it",
        revision="0" * 40,
        files={"model.safetensors": SHA, "tokenizer.json": "b" * 64},
    )
    closure = m.RuntimeClosure(
        nix_store_path="/nix/store/" + "a" * 32 + "-vllm-env",
        nar_hash="sha256-abc",
        packages={"vllm": "0.24.0"},
    )
    hardware = m.Hardware(
        gpu_name="NVIDIA GeForce RTX 5090",
        compute_capability="12.0",
        driver_version="580.1",
    )
    load = m.LoadParams(
        dtype="bfloat16",
        max_model_len=8192,
        attention_backend="FLASH_ATTN",
        batch_invariant=True,
        env={"VLLM_USE_FLASHINFER_SAMPLER": "0"},
        generation_config="vllm",
        enable_prefix_caching=False,
    )
    engine = m.LocalEngine(
        artifact=artifact.digest,
        closure=closure.digest,
        hardware=hardware.digest,
        load=load.digest,
    )
    return {
        "artifact": artifact,
        "closure": closure,
        "hardware": hardware,
        "load": load,
        "engine": engine,
    }


SCHEMA = {
    "type": "object",
    "properties": {"differentials": {"type": "array", "items": {"type": "string"}}},
    "required": ["differentials"],
}


@pytest.fixture
def sections():
    return dict(
        instructions=m.InstructionsSection(
            content=[
                "You are an emergency physician.\n",
                m.Slot(name="output_guidance"),
            ]
        ),
        case_template=m.CaseTemplateSection(
            content=["Case:\n", m.Slot(name="case"), "\n\n", m.Slot(name="appendices")],
            appendix_layout=m.AppendixLayout(heading_prefix="## "),
        ),
        output=m.OutputContract(
            mode="native",
            json_schema=SCHEMA,
            fills={"output_guidance": "Answer with a ranked list of differentials."},
        ),
        sampling=m.SamplingSection(
            temperature=0.7,
            top_p=0.95,
            max_output_tokens=1024,
            replicates=3,
            seeds=(11, 12, 13),
        ),
    )


@pytest.fixture
def world(engine_parts, sections):
    skeleton = m.resolve_request(**sections)
    source = m.CaseSource(source_id="ed-vignettes", hmac_key_id="k1")
    norm = m.Normalization(
        ops=(
            "newlines_lf",
            "strip_trailing_whitespace",
            "collapse_blank_lines",
            "strip",
        )
    )
    app = m.Appendix(title="Demographics", body="Age 67, male")
    raw = "Pt w/ CP  \r\n\r\n\r\n\r\nTrop 45 ng/L\r\n"
    case = m.CaseInput(
        source=source.digest,
        case_id="c1",
        raw_hmac=m.keyed_hmac(KEY, raw),
        normalization=norm.digest,
        appendices=(app.digest,),
    )
    case_set = m.CaseSet(cases=[case.digest])
    trial = m.Trial(
        engine=engine_parts["engine"].digest,
        request=skeleton.digest,
        cases=case_set.digest,
    )
    store = {
        c.digest: c
        for c in [
            *engine_parts.values(),
            skeleton,
            source,
            norm,
            app,
            case,
            case_set,
            trial,
        ]
    }
    return dict(
        skeleton=skeleton,
        source=source,
        norm=norm,
        app=app,
        case=case,
        raw=raw,
        case_set=case_set,
        trial=trial,
        store=store,
        **engine_parts,
    )


# -- identity -----------------------------------------------------------------------------


def test_digest_stable_and_content_based(engine_parts):
    a = engine_parts["hardware"]
    b = m.Hardware(
        driver_version="580.1",
        compute_capability="12.0",
        gpu_name="NVIDIA GeForce RTX 5090",
    )
    assert a.digest == b.digest and a.digest.startswith("sha256:")
    assert (
        m.Hardware(
            gpu_name="x", compute_capability="8.6", driver_version="580.1"
        ).digest
        != a.digest
    )


def test_roundtrip_through_json_keeps_digest(world):
    for comp in world["store"].values():
        again = m.load_component(comp.model_dump_json())
        assert again.digest == comp.digest


def test_frozen_and_extra_forbidden():
    h = m.Hardware(gpu_name="x", compute_capability="8.6", driver_version="1")
    with pytest.raises(ValidationError):
        h.gpu_name = "y"
    with pytest.raises(ValidationError):
        m.Hardware(
            gpu_name="x", compute_capability="8.6", driver_version="1", label="nice"
        )


def test_nan_rejected():
    with pytest.raises(ValidationError):
        m.SamplingSection(temperature=float("nan"))


# -- canonicalization ---------------------------------------------------------------------


def test_greedy_drops_seed_and_truncation_params():
    findings = []
    s = m.SamplingSection.model_validate(
        {"temperature": 0, "top_p": 0.9, "seeds": [1, 2], "replicates": 2},
        context={"findings": findings},
    )
    plain = m.SamplingSection(temperature=0.0, replicates=2)
    assert s.seeds is None and s.top_p is None and s.digest == plain.digest
    assert findings[0].code == "sampling.greedy_canonicalized"
    assert "seed" not in m.resolve_request(
        case_template=m.CaseTemplateSection(
            content=[m.Slot(name="case")], appendix_layout=m.AppendixLayout()
        ),
        output=m.OutputContract(mode="text"),
        sampling=s,
    ).render("x", 1, {"case": "c"})


def test_greedy_canonicalization_warns_without_context():
    with pytest.warns(m.ManifestWarning):
        m.SamplingSection(temperature=0, seeds=(1,))


def test_segments_merge_text():
    a = m.InstructionsSection(content=["a", "b", "", m.Text(text="c")])
    b = m.InstructionsSection(content="abc")
    assert a.digest == b.digest


def test_case_set_order_irrelevant(world):
    d1, d2 = world["case"].digest, "sha256:" + "f" * 64
    assert m.CaseSet(cases=[d1, d2]).digest == m.CaseSet(cases=[d2, d1]).digest


def test_seeds_must_match_replicates():
    with pytest.raises(ValidationError):
        m.SamplingSection(temperature=1.0, replicates=2, seeds=(1,))


# -- resolution ---------------------------------------------------------------------------


def test_resolution_fills_guidance_and_builds_body(world):
    sk = world["skeleton"]
    system = sk.messages[0].content
    assert system == (
        m.Text(
            text="You are an emergency physician.\nAnswer with a ranked list of differentials."
        ),
    )
    assert sk.runtime_slots() == {"case", "appendices"}
    assert sk.body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert sk.body["max_completion_tokens"] == 1024


def test_same_resolution_from_different_chunks_is_same_skeleton(sections):
    a = m.resolve_request(**sections)
    alt = dict(sections)
    alt["instructions"] = m.InstructionsSection(
        content=[
            "You are an emergency physician.\nAnswer with a ranked list of differentials."
        ]
    )
    alt["output"] = m.OutputContract(mode="native", json_schema=SCHEMA)
    assert m.resolve_request(**alt).digest == a.digest


def test_resolution_errors(sections):
    s = dict(sections, output=m.OutputContract(mode="native", json_schema=SCHEMA))
    with pytest.raises(m.ResolutionError, match="no fill"):
        m.resolve_request(**s)
    s = dict(sections, reasoning=m.ReasoningSection(fills={"output_guidance": "x"}))
    with pytest.raises(m.ResolutionError, match="filled by both"):
        m.resolve_request(**s)
    s = dict(sections, extra=m.ExtraBodySection(body={"temperature": 0.1}))
    with pytest.raises(m.ResolutionError, match="body keys"):
        m.resolve_request(**s)
    s = dict(sections, reasoning=m.ReasoningSection(fills={"case": "x"}))
    with pytest.raises(m.ResolutionError, match="runtime slot"):
        m.resolve_request(**s)
    with pytest.raises(ValidationError):
        m.ExtraBodySection(body={"seed": 1})


def test_tool_mode_body():
    oc = m.OutputContract(mode="tool", json_schema=SCHEMA, strict=True)
    frag = oc.body_fragment()
    assert frag["tool_choice"]["function"]["name"] == "final_result"
    assert frag["tools"][0]["function"]["parameters"] == SCHEMA
    with pytest.raises(ValidationError):
        m.OutputContract(mode="tool")


def test_render_wire_body(world):
    sk = world["skeleton"]
    fills, observed, findings = m.prepare_case(
        world["raw"],
        case_input=world["case"],
        normalization=world["norm"],
        appendices=[world["app"]],
        layout=sk.appendix_layout,
        hmac_key=KEY,
    )
    assert findings == [] and observed == world["case"].raw_hmac
    assert fills == {
        "case": "Pt w/ CP\n\nTrop 45 ng/L",
        "appendices": "## Demographics\nAge 67, male",
    }
    body = sk.render(world["engine"].digest, 2, fills)
    assert body["seed"] == 13 and body["model"] == world["engine"].digest
    assert (
        body["messages"][1]["content"]
        == "Case:\nPt w/ CP\n\nTrop 45 ng/L\n\n## Demographics\nAge 67, male"
    )
    with pytest.raises(ValueError, match="missing"):
        sk.render("x", 0, {"case": "c"})
    with pytest.raises(IndexError):
        sk.render("x", 3, fills)


def test_braces_in_clinical_text_are_inert(world):
    body = world["skeleton"].render(
        "x", 0, {"case": "{temp} {{38.5}} {% raw %}", "appendices": ""}
    )
    assert "{temp} {{38.5}} {% raw %}" in body["messages"][1]["content"]


def test_case_drift_is_a_warning_not_an_error(world):
    fills, observed, findings = m.prepare_case(
        world["raw"] + "edit",
        case_input=world["case"],
        normalization=world["norm"],
        appendices=[world["app"]],
        layout=world["skeleton"].appendix_layout,
        hmac_key=KEY,
    )
    assert [f.code for f in findings] == ["case.drift"] and "case" in fills


def test_nfkc_flagged_and_nfc_safe():
    n = m.Normalization(ops=("unicode_nfkc",))
    assert n.apply("5 µmol/L, 10²") == "5 μmol/L, 102"
    assert any(f.code == "normalization.nfkc" for f in n.lint())
    assert m.Normalization(ops=("unicode_nfc",)).apply("10²") == "10²"


# -- engine -------------------------------------------------------------------------------


def test_bitwise_assessment(engine_parts):
    p = engine_parts
    tier, _ = m.assess_engine(
        p["engine"], closure=p["closure"], hardware=p["hardware"], load=p["load"]
    )
    assert tier == "bitwise"
    load = p["load"].model_copy(update={"env": {}})
    tier, f = m.assess_engine(
        p["engine"], closure=p["closure"], hardware=p["hardware"], load=load
    )
    assert tier == "best_effort" and any(
        x.code == "engine.flashinfer_sampler" for x in f
    )
    remote = m.RemoteEngine(base_url="https://gemma.example.org/v1/", model="gemma")
    assert remote.base_url == "https://gemma.example.org/v1"
    assert m.assess_engine(remote)[0] == "best_effort"


def test_vllm_launch(engine_parts):
    p = engine_parts
    launch = m.vllm_launch(p["engine"], p["artifact"], p["load"])
    a = launch.argv
    assert a[:3] == ("vllm", "serve", "google/gemma-3-4b-it")
    assert a[a.index("--served-model-name") + 1] == p["engine"].digest
    assert a[a.index("--revision") + 1] == "0" * 40
    assert "--no-enable-prefix-caching" in a and "--attention-backend" in a
    assert launch.env == {
        "VLLM_USE_FLASHINFER_SAMPLER": "0",
        "VLLM_BATCH_INVARIANT": "1",
    }
    with pytest.raises(ValueError):
        m.vllm_launch(
            p["engine"],
            p["artifact"],
            p["load"].model_copy(update={"chat_template": "{{x}}"}),
        )
    with pytest.raises(ValidationError):
        m.LoadParams(env={"VLLM_BATCH_INVARIANT": "1"})


def test_attestation_mismatch_warns(engine_parts):
    p = engine_parts
    att = m.EngineAttestation(
        engine=p["engine"].digest,
        phase="start",
        observed_at=datetime.now(timezone.utc),
        served_model_name="something-else",
        gpu_name="NVIDIA GeForce RTX 3070",
        packages={"vllm": "0.24.0"},
    )
    codes = {
        f.code
        for f in m.verify_attestation(
            att, p["engine"], closure=p["closure"], hardware=p["hardware"]
        )
    }
    assert codes == {"attest.served_name", "attest.gpu"}


def test_canary_drift():
    base = dict(canary_set="sha256:" + "1" * 64, engine="sha256:" + "2" * 64)
    a = m.CanaryResult(
        phase="start", digests=(m.canary_digest("x", None, None, "stop"),), **base
    )
    b = m.CanaryResult(
        phase="end", digests=(m.canary_digest("y", None, None, "stop"),), **base
    )
    assert (
        m.compare_canaries(a, a) == []
        and m.compare_canaries(a, b)[0].code == "canary.drift"
    )


# -- trial / bundle -----------------------------------------------------------------------


def test_schedule_orders():
    p = m.ExecutionPolicy()
    assert p.schedule(["a", "b"], 2) == [("a", 0), ("a", 1), ("b", 0), ("b", 1)]
    assert m.ExecutionPolicy(order="replicate_major").schedule(["a", "b"], 2) == [
        ("a", 0),
        ("b", 0),
        ("a", 1),
        ("b", 1),
    ]
    sh = m.ExecutionPolicy(order="shuffled", shuffle_seed=7)
    assert sh.schedule(["a", "b", "c"], 2) == sh.schedule(["a", "b", "c"], 2)
    assert sorted(sh.schedule(["a", "b", "c"], 2)) == p.schedule(["a", "b", "c"], 2)
    with pytest.raises(ValidationError):
        m.ExecutionPolicy(order="shuffled")


def test_bundle_collect_verify_materialize(world):
    b = m.Bundle.collect(world["trial"], world["store"])
    assert len(b.components) == 12
    again = m.Bundle.model_validate_json(b.to_json())
    assert again.root == world["trial"].digest
    doc = b.materialize()
    assert doc["engine"]["load"]["batch_invariant"] is True
    assert doc["cases"]["cases"][0]["appendices"][0]["title"] == "Demographics"


def test_bundle_rejects_tampering(world):
    b = m.Bundle.collect(world["trial"], world["store"])
    data = json.loads(b.to_json())
    data["components"][world["hardware"].digest]["driver_version"] = "999"
    with pytest.raises(ValidationError, match="digest mismatch"):
        m.Bundle.model_validate(data)
    data = json.loads(b.to_json())
    del data["components"][world["norm"].digest]
    with pytest.raises(ValidationError, match="references missing"):
        m.Bundle.model_validate(data)


def test_lint_is_soft(world):
    store = dict(world["store"])
    case = world["case"].model_copy(update={"raw_hmac": None})
    cs = m.CaseSet(cases=[case.digest])
    trial = world["trial"].model_copy(
        update={"cases": cs.digest, "execution": m.ExecutionPolicy(max_concurrency=8)}
    )
    store.update({c.digest: c for c in (case, cs, trial)})
    codes = {f.code for f in m.Bundle.collect(trial, store).lint()}
    assert "case.no_hmac" in codes
    assert "trial.concurrency" not in codes  # bitwise engine


def test_governance_blocks(world):
    ctx = m.BundleContext(
        endpoints=(
            m.EndpointBinding(
                engine=world["engine"].digest, base_url="http://gpu5090:8000/v1"
            ),
        ),
        case_policies=(
            m.CaseSourcePolicy(
                source=world["source"].digest,
                required_clearances=frozenset({"clinical"}),
            ),
        ),
    )
    b = m.Bundle.collect(world["trial"], world["store"], context=ctx)
    with pytest.raises(m.GovernanceViolation) as e:
        b.check_governance(world["trial"].digest)
    assert e.value.findings[0].severity == "block"
    ok = ctx.model_copy(
        update={
            "endpoints": (
                m.EndpointBinding(
                    engine=world["engine"].digest,
                    base_url="x",
                    clearances=frozenset({"clinical"}),
                ),
            )
        }
    )
    m.Bundle.collect(world["trial"], world["store"], context=ok).check_governance(
        world["trial"].digest
    )


def test_governance_covers_judges(world):
    judge_engine = m.RemoteEngine(base_url="https://judge.example.org/v1", model="big")
    judge_sk = m.resolve_request(
        purpose="judge",
        case_template=m.CaseTemplateSection(
            content=[
                "Output:\n",
                m.Slot(name="output"),
                "\nExpected:\n",
                m.Slot(name="expectation"),
            ]
        ),
        output=m.OutputContract(mode="native", json_schema={"type": "object"}),
        sampling=m.SamplingSection(temperature=0),
    )
    es = m.ExpectationSchema(json_schema={"type": "object"})
    scorer = m.ScorerSpec(
        code=m.CodeIdentity(package="scorer", version="0.1", source_hash="abc"),
        expectation_schema=es.digest,
        views=(
            m.ScoringView(
                name="dx_match",
                output_pointer="/differentials",
                expectation_pointer="/differentials",
                metric=m.TextMatchMetric(function="ranked_overlap", params={"k": 5}),
            ),
            m.ScoringView(
                name="judge",
                metric=m.JudgeMetric(
                    engine=judge_engine.digest, request=judge_sk.digest
                ),
            ),
        ),
    )
    store = dict(world["store"])
    store.update({c.digest: c for c in (judge_engine, judge_sk, es, scorer)})
    ctx = m.BundleContext(
        endpoints=(
            m.EndpointBinding(
                engine=world["engine"].digest,
                base_url="x",
                clearances=frozenset({"clinical"}),
            ),
            m.EndpointBinding(
                engine=judge_engine.digest, base_url=judge_engine.base_url
            ),
        ),
        case_policies=(
            m.CaseSourcePolicy(
                source=world["source"].digest,
                required_clearances=frozenset({"clinical"}),
            ),
        ),
    )
    b = m.Bundle(
        root=world["trial"].digest,
        components={
            **m.Bundle.collect(world["trial"], store).components,
            **m.Bundle.collect(scorer, store).components,
        },
        context=ctx,
    )
    b.check_governance(world["trial"].digest)
    with pytest.raises(m.GovernanceViolation):
        b.check_governance(world["trial"].digest, scorer_digest=scorer.digest)


def test_expectation_set_rejects_duplicate_cases(world):
    es = m.ExpectationSchema(json_schema={})
    e1 = m.Expectation(
        expectation_schema=es.digest,
        source=world["source"].digest,
        case_id="c1",
        payload={"a": 1},
    )
    e2 = m.Expectation(
        expectation_schema=es.digest,
        source=world["source"].digest,
        case_id="c1",
        payload={"a": 2},
    )
    s = m.ExpectationSet(items=[e1.digest, e2.digest])
    store = {c.digest: c for c in (es, e1, e2, s, world["source"])}
    with pytest.raises(ValidationError, match="two expectations"):
        m.Bundle.collect(s, store)


# -- scoring helpers ----------------------------------------------------------------------


def test_best_effort_parsing():
    p = m.ParsePolicy()
    assert p.parse('{"a": 1}') == ({"a": 1}, "parsed")
    assert p.parse('Sure!\n```json\n{"a": 2}\n```') == ({"a": 2}, "extracted")
    assert p.parse('Here: {"a": 3} hope it helps') == ({"a": 3}, "extracted")
    v, status = p.parse("no json here")
    assert m.is_missing(v) and status == "unparseable"
    assert m.ParsePolicy(strategy="strict").parse('x {"a":1}')[1] == "unparseable"
    assert m.ParsePolicy().parse("  ")[1] == "no_output"


def test_json_pointer():
    doc = {"differentials": [{"name": "ACS"}], "a/b": {"~k": 1}}
    assert m.resolve_pointer(doc, "/differentials/0/name") == "ACS"
    assert m.resolve_pointer(doc, "/a~1b/~0k") == 1
    assert m.is_missing(m.resolve_pointer(doc, "/differentials/5"))
    assert m.resolve_pointer(doc, "") is doc


def test_json_schemas_generate():
    schemas = m.component_json_schemas()
    assert "sampling" in schemas and "properties" in schemas["load_params"]
    assert schemas["load_params"]["properties"]["generation_config"][
        "description"
    ].startswith("vLLM 0.24")


def test_run_record_in_bundle(world):
    comp = m.Completion(
        status="ok", content='{"differentials": ["ACS"]}', finish_reason="stop"
    )
    item = m.RunItem(
        case_input=world["case"].digest,
        replicate=0,
        seed=11,
        observed_raw_hmac=world["case"].raw_hmac,
        request_hmac=m.keyed_hmac(KEY, "body"),
        completion=comp,
    )
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    run = m.RunRecord(
        trial=world["trial"].digest, started_at=now, finished_at=now, items=(item,)
    )
    store = dict(world["store"], **{run.digest: run})
    b = m.Bundle.collect(run, store)
    assert (
        m.Bundle.model_validate_json(b.to_json()).components[run.digest].digest
        == run.digest
    )
