-- src/chatddx/catalog/families.py: plan_repair keeps the source and changes exactly one of the
-- id or the fingerprint. 0022's check, after 0025 renamed the column.
CREATE OR REPLACE FUNCTION catalog.check_binding() RETURNS trigger LANGUAGE plpgsql AS $$
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
