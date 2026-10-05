-- src/chatddx/catalog/model.py: THREAD_KINDS
ALTER TABLE catalog.thread
    ADD CONSTRAINT thread_kind_check CHECK (kind IN (
        'chunk.instructions', 'chunk.few_shot', 'chunk.prompt', 'chunk.output',
        'chunk.sampling', 'chunk.reasoning', 'chunk.passthrough', 'chunk.translations',
        'chunk.toolset', 'tool', 'skeleton', 'trial', 'judge', 'scorer', 'scoring',
        'appendix', 'expectation', 'model', 'engine.local', 'engine.remote',
        'expectation_schema', 'canary_set'
    ));

-- src/chatddx/catalog/model.py: EntryField, Entry._shape and LANGUAGE, and check_entry in
-- src/chatddx/catalog/language.py.
-- A check passes when it is NULL, so NULL values are ruled out explicitly.
ALTER TABLE catalog.entry
    ADD CONSTRAINT entry_field_check CHECK (field IN (
        'name', 'description', 'tag', 'owner', 'collaborator', 'deleted', 'language'
    )),
    ADD CONSTRAINT entry_shape_check CHECK (CASE field
        WHEN 'name' THEN person IS NULL AND CASE
            WHEN present THEN value IS NOT NULL AND value <> ''
            ELSE value IS NULL
        END
        WHEN 'description' THEN value IS NOT NULL AND person IS NULL AND present
        WHEN 'tag' THEN value IS NOT NULL AND value <> '' AND person IS NULL
        WHEN 'owner' THEN value IS NULL AND person IS NOT NULL AND present
        WHEN 'collaborator' THEN value IS NULL AND person IS NOT NULL
        WHEN 'deleted' THEN value IS NULL AND person IS NULL
        WHEN 'language' THEN value IS NOT NULL
            AND value ~ '^[a-z]{2,3}(-[A-Za-z0-9]{1,8})*$'
            AND person IS NULL AND present
    END),
    ADD CONSTRAINT entry_language_check CHECK (field <> 'language' OR family IS NOT NULL);

-- src/chatddx/catalog/model.py: check_label repeats this check.
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

-- src/chatddx/catalog/threads.py: check_based_on repeats this check.
CREATE FUNCTION catalog.check_based_on() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.based_on IS NOT NULL AND NOT EXISTS (
        SELECT FROM catalog.thread t
        JOIN catalog.edit o ON o.id = t.forked_from
        JOIN catalog.edit b ON b.thread = o.thread AND b.id >= o.id
        WHERE t.id = NEW.thread AND b.id = NEW.based_on
    ) THEN
        RAISE EXCEPTION 'edit % is not in the origin of thread %', NEW.based_on, NEW.thread;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER edit_based_on BEFORE INSERT ON catalog.edit
FOR EACH ROW EXECUTE FUNCTION catalog.check_based_on();

-- src/chatddx/catalog/families.py: plan_repair keeps the source and changes exactly one of the
-- id or the fingerprint.
CREATE FUNCTION catalog.check_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    previous catalog.binding;
BEGIN
    SELECT * INTO previous FROM catalog.binding
    WHERE family = NEW.family ORDER BY id DESC LIMIT 1;
    IF FOUND AND NOT (
        previous.source = NEW.source
        AND (previous.source_id = NEW.source_id OR previous.fingerprint = NEW.fingerprint)
    ) THEN
        RAISE EXCEPTION 'family %: a new binding must keep the source, and the id or the fingerprint',
            NEW.family;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER binding_anchor BEFORE INSERT ON catalog.binding
FOR EACH ROW EXECUTE FUNCTION catalog.check_binding();

-- src/chatddx/catalog/model.py: LANGUAGE and LANGUAGE_KINDS, and check_language in
-- src/chatddx/catalog/language.py.
ALTER TABLE catalog.language
    ADD CONSTRAINT language_value_check CHECK (value ~ '^[a-z]{2,3}(-[A-Za-z0-9]{1,8})*$'),
    ADD CONSTRAINT language_kind_check CHECK (kind IN (
        'chunk.instructions', 'chunk.few_shot', 'chunk.prompt', 'chunk.output',
        'chunk.toolset', 'chunk.translations', 'skeleton'
    ));

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

DO $$
DECLARE
    t regclass;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'catalog.thread', 'catalog.edit', 'catalog.family', 'catalog.binding',
        'catalog.entry', 'catalog.label', 'catalog.language'
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
