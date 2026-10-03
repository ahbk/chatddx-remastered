-- src/chatddx/core/catalog.py: THREAD_KINDS
ALTER TABLE catalog.thread ADD CHECK (kind IN (
    'chunk.instructions', 'chunk.few_shot', 'chunk.prompt', 'chunk.output',
    'chunk.sampling', 'chunk.reasoning', 'chunk.passthrough', 'skeleton', 'trial',
    'judge', 'scorer', 'scoring', 'appendix', 'expectation'
));

-- src/chatddx/core/catalog.py: EntryField and Entry._shape
ALTER TABLE catalog.entry
    ADD CHECK (field IN ('name', 'description', 'tag', 'owner', 'collaborator', 'deleted')),
    ADD CHECK (CASE field
        WHEN 'name' THEN value <> '' AND person IS NULL AND present
        WHEN 'description' THEN value IS NOT NULL AND person IS NULL AND present
        WHEN 'tag' THEN value <> '' AND person IS NULL
        WHEN 'owner' THEN value IS NULL AND person IS NOT NULL AND present
        WHEN 'collaborator' THEN value IS NULL AND person IS NOT NULL
        WHEN 'deleted' THEN value IS NULL AND person IS NULL
    END);

CREATE FUNCTION catalog.check_label() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    SELECT coalesce(jsonb_array_length(doc -> (NEW.part || 's')), 0) INTO n
    FROM factor.component WHERE digest = NEW.scorer;
    IF NEW.position >= n THEN
        RAISE EXCEPTION '%: no % at position %', NEW.scorer, NEW.part, NEW.position;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER label_position BEFORE INSERT ON catalog.label
FOR EACH ROW EXECUTE FUNCTION catalog.check_label();

DO $$
DECLARE
    t regclass;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'catalog.thread', 'catalog.edit', 'catalog.entry', 'catalog.label'
    ]::regclass[] LOOP
        EXECUTE format(
            'CREATE TRIGGER insert_only BEFORE UPDATE OR DELETE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION factor.refuse_change()', t);
        EXECUTE format(
            'CREATE TRIGGER insert_only_truncate BEFORE TRUNCATE ON %s '
            'FOR EACH STATEMENT EXECUTE FUNCTION factor.refuse_change()', t);
    END LOOP;
END
$$;
