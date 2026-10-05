CREATE SCHEMA factor;

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
