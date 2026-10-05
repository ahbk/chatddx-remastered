-- src/chatddx/store/catalog.py: Catalog.language_of takes a component's latest row here as its language.
CREATE TABLE catalog.language (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    digest text NOT NULL REFERENCES factor.component,
    value text NOT NULL,
    by bigint NOT NULL REFERENCES identity.person,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX language_digest ON catalog.language (digest, id);
