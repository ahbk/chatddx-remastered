CREATE SCHEMA identity;

CREATE TABLE identity.person (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL
);
