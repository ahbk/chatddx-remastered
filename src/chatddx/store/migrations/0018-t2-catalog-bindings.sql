-- src/chatddx/store/catalog.py: Catalog.repair changes exactly one of the two.
CREATE FUNCTION catalog.check_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    previous catalog.binding;
BEGIN
    SELECT * INTO previous FROM catalog.binding
    WHERE family = NEW.family ORDER BY id DESC LIMIT 1;
    IF FOUND AND NOT (
        (previous.source = NEW.source AND previous.source_id = NEW.source_id)
        OR previous.vignette = NEW.vignette
    ) THEN
        RAISE EXCEPTION 'family %: a new binding must keep the id or the vignette', NEW.family;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER binding_anchor BEFORE INSERT ON catalog.binding
FOR EACH ROW EXECUTE FUNCTION catalog.check_binding();
