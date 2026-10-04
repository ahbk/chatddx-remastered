-- src/chatddx/store/catalog.py: Catalog.edit repeats this check.
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
