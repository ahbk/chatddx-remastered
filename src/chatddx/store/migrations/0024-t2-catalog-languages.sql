-- src/chatddx/catalog/model.py: LANGUAGE and LANGUAGE_KINDS, and check_language and check_entry in
-- src/chatddx/catalog/language.py. A component's language is kept on its digest in catalog.language; a
-- language entry is only for the vignette of a family.
ALTER TABLE catalog.language
    ADD CONSTRAINT language_value_check CHECK (value ~ '^[a-z]{2,3}(-[A-Za-z0-9]{1,8})*$'),
    ADD CONSTRAINT language_kind_check CHECK (kind IN (
        'chunk.instructions', 'chunk.few_shot', 'chunk.prompt', 'chunk.output',
        'chunk.toolset', 'chunk.translations', 'skeleton'
    ));

ALTER TABLE catalog.entry
    ADD CONSTRAINT entry_language_check CHECK (field <> 'language' OR family IS NOT NULL);

-- A skeleton compiled later still takes its language from its recipe; this only refuses what is
-- already known to be compiled.
CREATE FUNCTION catalog.check_language() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT FROM factor.component_ref r JOIN factor.component c ON c.digest = r.src
        WHERE c.kind = 'compilation' AND r.path = '/skeleton' AND r.dst = NEW.digest
    ) THEN
        RAISE EXCEPTION '%: a compiled skeleton''s language comes from its recipe', NEW.digest;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER language_compiled BEFORE INSERT ON catalog.language
FOR EACH ROW EXECUTE FUNCTION catalog.check_language();

CREATE TRIGGER insert_only BEFORE UPDATE OR DELETE ON catalog.language
FOR EACH ROW EXECUTE FUNCTION factor.refuse_change();
CREATE TRIGGER insert_only_truncate BEFORE TRUNCATE ON catalog.language
FOR EACH STATEMENT EXECUTE FUNCTION factor.refuse_change();
