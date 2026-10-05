-- src/chatddx/catalog/model.py: EntryField, Entry._shape and LANGUAGE.
-- The shape check from 0011 has a generated name, so it is found by its definition.
DO $$
DECLARE
    shape name;
BEGIN
    SELECT conname INTO STRICT shape FROM pg_constraint
    WHERE conrelid = 'catalog.entry'::regclass AND contype = 'c'
        AND pg_get_constraintdef(oid) LIKE '%CASE field%';
    EXECUTE format('ALTER TABLE catalog.entry DROP CONSTRAINT %I', shape);
END
$$;

ALTER TABLE catalog.entry
    DROP CONSTRAINT entry_field_check,
    ADD CONSTRAINT entry_field_check CHECK (field IN (
        'name', 'description', 'tag', 'owner', 'collaborator', 'deleted', 'language'
    )),
    ADD CONSTRAINT entry_shape_check CHECK (CASE field
        WHEN 'name' THEN value <> '' AND person IS NULL AND present
        WHEN 'description' THEN value IS NOT NULL AND person IS NULL AND present
        WHEN 'tag' THEN value <> '' AND person IS NULL
        WHEN 'owner' THEN value IS NULL AND person IS NOT NULL AND present
        WHEN 'collaborator' THEN value IS NULL AND person IS NOT NULL
        WHEN 'deleted' THEN value IS NULL AND person IS NULL
        WHEN 'language' THEN value ~ '^[a-z]{2,3}(-[A-Za-z0-9]{1,8})*$'
            AND person IS NULL AND present
    END);
