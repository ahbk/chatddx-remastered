CREATE SCHEMA factor;
CREATE SCHEMA ledger;

CREATE TABLE factor.component (
    digest text PRIMARY KEY,
    kind text NOT NULL,
    v integer NOT NULL,
    canonical text NOT NULL,
    doc jsonb NOT NULL
);
CREATE INDEX component_kind ON factor.component (kind);

CREATE TABLE factor.component_ref (
    src text NOT NULL REFERENCES factor.component DEFERRABLE INITIALLY DEFERRED,
    path text NOT NULL,
    dst text NOT NULL REFERENCES factor.component DEFERRABLE INITIALLY DEFERRED,
    kinds text[] NOT NULL,
    PRIMARY KEY (src, path)
);
CREATE INDEX component_ref_dst ON factor.component_ref (dst);

-- Item rows reference their log's started row; `stage` is a constant so that a
-- foreign key can point at it.
CREATE TABLE ledger.run_stage (
    run uuid NOT NULL,
    stage text NOT NULL CHECK (stage IN ('started', 'finished')),
    trial text REFERENCES factor.component,
    payload text NOT NULL,
    doc jsonb NOT NULL,
    PRIMARY KEY (run, stage),
    CHECK ((stage = 'started') = (trial IS NOT NULL))
);

CREATE TABLE ledger.run_item (
    run uuid NOT NULL,
    stage text NOT NULL DEFAULT 'started' CHECK (stage = 'started'),
    "case" text NOT NULL REFERENCES factor.component,
    replicate integer NOT NULL,
    payload text NOT NULL,
    doc jsonb NOT NULL,
    PRIMARY KEY (run, "case", replicate),
    FOREIGN KEY (run, stage) REFERENCES ledger.run_stage
);

CREATE TABLE ledger.canary_call (
    run uuid NOT NULL,
    stage text NOT NULL DEFAULT 'started' CHECK (stage = 'started'),
    phase text NOT NULL,
    canary integer NOT NULL,
    payload text NOT NULL,
    doc jsonb NOT NULL,
    PRIMARY KEY (run, phase, canary),
    FOREIGN KEY (run, stage) REFERENCES ledger.run_stage
);

CREATE TABLE ledger.score_stage (
    score uuid NOT NULL,
    stage text NOT NULL CHECK (stage IN ('started', 'finished')),
    run uuid,
    run_stage text CHECK (run_stage = 'started'),
    scoring text REFERENCES factor.component,
    payload text NOT NULL,
    doc jsonb NOT NULL,
    PRIMARY KEY (score, stage),
    FOREIGN KEY (run, run_stage) REFERENCES ledger.run_stage,
    CHECK (
        (stage = 'started')
        = (run IS NOT NULL AND run_stage IS NOT NULL AND scoring IS NOT NULL)
    )
);

CREATE TABLE ledger.score_item (
    score uuid NOT NULL,
    stage text NOT NULL DEFAULT 'started' CHECK (stage = 'started'),
    "case" text NOT NULL REFERENCES factor.component,
    replicate integer NOT NULL,
    view integer NOT NULL,
    payload text NOT NULL,
    doc jsonb NOT NULL,
    PRIMARY KEY (score, "case", replicate, view),
    FOREIGN KEY (score, stage) REFERENCES ledger.score_stage
);
