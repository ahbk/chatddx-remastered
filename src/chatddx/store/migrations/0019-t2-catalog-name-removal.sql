-- src/chatddx/catalog/model.py: EntryField, Entry._shape and LANGUAGE.
-- A name can be removed by an entry without a value; the thread is then shown by its title.
-- A check passes when it is NULL, so a NULL value is ruled out explicitly: 0017 let NULL
-- names, tags and languages through.
ALTER TABLE catalog.entry
    DROP CONSTRAINT entry_shape_check,
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
    END);
