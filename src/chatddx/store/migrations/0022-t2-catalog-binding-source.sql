-- src/chatddx/store/catalog.py: Catalog.repair keeps the source and changes exactly one of the
-- id or the vignette. 0018 let a binding change source as long as it kept the vignette.
CREATE OR REPLACE FUNCTION catalog.check_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    previous catalog.binding;
BEGIN
    SELECT * INTO previous FROM catalog.binding
    WHERE family = NEW.family ORDER BY id DESC LIMIT 1;
    IF FOUND AND NOT (
        previous.source = NEW.source
        AND (previous.source_id = NEW.source_id OR previous.vignette = NEW.vignette)
    ) THEN
        RAISE EXCEPTION 'family %: a new binding must keep the source, and the id or the vignette',
            NEW.family;
    END IF;
    RETURN NEW;
END
$$;
