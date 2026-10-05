ALTER TABLE ledger.run_stage ADD CHECK (
    doc = payload::jsonb
    AND run = (doc->>'run')::uuid
    AND stage = doc->>'stage'
    AND trial IS NOT DISTINCT FROM doc->>'trial'
);

ALTER TABLE ledger.run_item ADD CHECK (
    doc = payload::jsonb
    AND run = (doc->>'run')::uuid
    AND "case" = doc #>> '{key,case}'
    AND replicate = (doc #>> '{key,replicate}')::integer
);

ALTER TABLE ledger.canary_call ADD CHECK (
    doc = payload::jsonb
    AND run = (doc->>'run')::uuid
    AND phase = doc->>'phase'
    AND canary = (doc->>'canary')::integer
);

ALTER TABLE ledger.score_stage ADD CHECK (
    doc = payload::jsonb
    AND score = (doc->>'score')::uuid
    AND stage = doc->>'stage'
    AND run IS NOT DISTINCT FROM (doc->>'run')::uuid
    AND scoring IS NOT DISTINCT FROM doc->>'scoring'
);

ALTER TABLE ledger.score_item ADD CHECK (
    doc = payload::jsonb
    AND score = (doc->>'score')::uuid
    AND "case" = doc #>> '{key,case}'
    AND replicate = (doc #>> '{key,replicate}')::integer
    AND view = (doc->>'view')::integer
);

DO $$
DECLARE
    t regclass;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'ledger.run_stage', 'ledger.run_item', 'ledger.canary_call',
        'ledger.score_stage', 'ledger.score_item'
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
