ALTER TABLE identity.person
    ADD CHECK (login ~ '^[a-z0-9][a-z0-9._@-]*$'),
    -- src/chatddx/core/identity.py: Role
    ADD CHECK (roles <@ ARRAY['admin', 'clinician', 'developer', 'ops', 'researcher']);

ALTER TABLE identity.session ADD CHECK (expires > created);
