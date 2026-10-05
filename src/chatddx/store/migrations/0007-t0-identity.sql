CREATE SCHEMA identity;

CREATE TABLE identity.person (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    login text NOT NULL UNIQUE,
    roles text[] NOT NULL DEFAULT '{}',
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE identity.credential (
    person bigint PRIMARY KEY REFERENCES identity.person,
    hash text NOT NULL
);

-- Only a digest of the token is stored; the token itself lives in the client's cookie.
CREATE TABLE identity.session (
    token_digest text PRIMARY KEY,
    person bigint NOT NULL REFERENCES identity.person,
    created timestamptz NOT NULL DEFAULT now(),
    expires timestamptz NOT NULL
);
CREATE INDEX session_person ON identity.session (person);
