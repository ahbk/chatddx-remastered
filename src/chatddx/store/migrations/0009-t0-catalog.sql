CREATE SCHEMA catalog;

-- Targets for the composite foreign keys below: a thread's edits point at components of
-- the thread's kind, and an edit's compilation is a compilation of the edit's skeleton.
ALTER TABLE factor.component ADD UNIQUE (digest, kind);
ALTER TABLE factor.component_ref ADD UNIQUE (src, path, dst);

CREATE TABLE catalog.thread (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL,
    forked_from bigint,
    by bigint NOT NULL REFERENCES identity.person,
    at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, kind)
);

-- src/chatddx/store/catalog.py: _HEAD takes the edit with the highest id as a thread's head.
CREATE TABLE catalog.edit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread bigint NOT NULL,
    kind text NOT NULL,
    digest text NOT NULL,
    compilation text,
    compilation_kind text NOT NULL DEFAULT 'compilation'
        CHECK (compilation_kind = 'compilation'),
    compilation_path text NOT NULL DEFAULT '/skeleton'
        CHECK (compilation_path = '/skeleton'),
    by bigint NOT NULL REFERENCES identity.person,
    at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, kind),
    FOREIGN KEY (thread, kind) REFERENCES catalog.thread (id, kind),
    FOREIGN KEY (digest, kind) REFERENCES factor.component (digest, kind),
    FOREIGN KEY (compilation, compilation_kind) REFERENCES factor.component (digest, kind),
    FOREIGN KEY (compilation, compilation_path, digest)
        REFERENCES factor.component_ref (src, path, dst),
    CHECK (compilation IS NULL OR kind = 'skeleton')
);
CREATE INDEX edit_thread ON catalog.edit (thread, id);
CREATE INDEX edit_digest ON catalog.edit (digest);

ALTER TABLE catalog.thread
    ADD FOREIGN KEY (forked_from, kind) REFERENCES catalog.edit (id, kind);

-- src/chatddx/core/catalog.py: About.of folds a subject's entries in id order.
CREATE TABLE catalog.entry (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread bigint REFERENCES catalog.thread,
    run uuid,
    run_stage text CHECK (run_stage = 'started'),
    score uuid,
    score_stage text CHECK (score_stage = 'started'),
    field text NOT NULL,
    value text,
    person bigint REFERENCES identity.person,
    present boolean NOT NULL DEFAULT true,
    by bigint NOT NULL REFERENCES identity.person,
    at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (run, run_stage) REFERENCES ledger.run_stage,
    FOREIGN KEY (score, score_stage) REFERENCES ledger.score_stage,
    CHECK (num_nonnulls(thread, run, score) = 1),
    CHECK ((run IS NULL) = (run_stage IS NULL)),
    CHECK ((score IS NULL) = (score_stage IS NULL))
);
CREATE INDEX entry_thread ON catalog.entry (thread) WHERE thread IS NOT NULL;
CREATE INDEX entry_run ON catalog.entry (run) WHERE run IS NOT NULL;
CREATE INDEX entry_score ON catalog.entry (score) WHERE score IS NOT NULL;

-- Positions only mean something inside one scorer digest, so labels key on it.
CREATE TABLE catalog.label (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scorer text NOT NULL,
    kind text NOT NULL DEFAULT 'scorer' CHECK (kind = 'scorer'),
    part text NOT NULL CHECK (part IN ('view', 'resource')),
    position integer NOT NULL CHECK (position >= 0),
    value text NOT NULL,
    by bigint NOT NULL REFERENCES identity.person,
    at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (scorer, kind) REFERENCES factor.component (digest, kind)
);
CREATE INDEX label_scorer ON catalog.label (scorer);
