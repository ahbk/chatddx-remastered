-- src/chatddx/core/catalog.py: LANGUAGE, and Catalog.note in src/chatddx/store/catalog.py.
-- A component's language is kept on its digest in catalog.language; a language entry is only
-- for the vignette of a family.
ALTER TABLE catalog.language
    ADD CONSTRAINT language_value_check CHECK (value ~ '^[a-z]{2,3}(-[A-Za-z0-9]{1,8})*$');

ALTER TABLE catalog.entry
    ADD CONSTRAINT entry_language_check CHECK (field <> 'language' OR family IS NOT NULL);

CREATE TRIGGER insert_only BEFORE UPDATE OR DELETE ON catalog.language
FOR EACH ROW EXECUTE FUNCTION factor.refuse_change();
CREATE TRIGGER insert_only_truncate BEFORE TRUNCATE ON catalog.language
FOR EACH STATEMENT EXECUTE FUNCTION factor.refuse_change();
