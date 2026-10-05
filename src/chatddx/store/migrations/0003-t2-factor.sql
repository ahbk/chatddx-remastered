ALTER TABLE factor.component ADD CHECK (
    digest = 'sha256:' || encode(sha256(convert_to(canonical, 'UTF8')), 'hex')
    AND doc = canonical::jsonb
    AND kind = doc->>'kind'
    AND v = (doc->>'v')::integer
);

-- Deferred because a reference may be inserted before the component it points at.
CREATE FUNCTION factor.check_ref() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    dst_kind text;
    at_path text;
BEGIN
    SELECT kind INTO dst_kind FROM factor.component WHERE digest = NEW.dst;
    IF NOT dst_kind = ANY (NEW.kinds) THEN
        RAISE EXCEPTION '%: % is not one of %', NEW.src || NEW.path, dst_kind, NEW.kinds;
    END IF;
    SELECT doc #>> string_to_array(ltrim(NEW.path, '/'), '/') INTO at_path
    FROM factor.component WHERE digest = NEW.src;
    IF at_path IS DISTINCT FROM NEW.dst THEN
        RAISE EXCEPTION '%: component holds %, not %', NEW.src || NEW.path, at_path, NEW.dst;
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER component_ref_matches
AFTER INSERT ON factor.component_ref
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION factor.check_ref();

-- Every schema's insert-only triggers call this.
CREATE FUNCTION factor.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%.% is insert-only', TG_TABLE_SCHEMA, TG_TABLE_NAME;
END
$$;

DO $$
DECLARE
    t regclass;
BEGIN
    FOREACH t IN ARRAY ARRAY['factor.component', 'factor.component_ref']::regclass[] LOOP
        EXECUTE format(
            'CREATE TRIGGER insert_only BEFORE UPDATE OR DELETE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION factor.refuse_change()', t);
        EXECUTE format(
            'CREATE TRIGGER insert_only_truncate BEFORE TRUNCATE ON %s '
            'FOR EACH STATEMENT EXECUTE FUNCTION factor.refuse_change()', t);
    END LOOP;
END
$$;
