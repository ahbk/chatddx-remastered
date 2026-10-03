CREATE TABLE catalog.family (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    by bigint NOT NULL REFERENCES identity.person,
    at timestamptz NOT NULL DEFAULT now()
);

-- src/chatddx/store/catalog.py: Catalog.family matches a case to a binding by source, id and
-- vignette fingerprint; the binding with the highest id is where the family's vignette is now.
CREATE TABLE catalog.binding (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    family bigint NOT NULL REFERENCES catalog.family,
    source text NOT NULL,
    source_id text NOT NULL,
    vignette jsonb NOT NULL,
    by bigint NOT NULL REFERENCES identity.person,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX binding_family ON catalog.binding (family, id);
CREATE INDEX binding_source ON catalog.binding (source, source_id);

-- entry_check is 0009-t0-catalog.sql's unnamed num_nonnulls(thread, run, score) check.
ALTER TABLE catalog.entry
    ADD COLUMN family bigint REFERENCES catalog.family,
    DROP CONSTRAINT entry_check,
    ADD CHECK (num_nonnulls(thread, family, run, score) = 1);
CREATE INDEX entry_family ON catalog.entry (family) WHERE family IS NOT NULL;
