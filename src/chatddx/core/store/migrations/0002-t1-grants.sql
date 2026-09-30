-- Roles are cluster-wide, so another database may already have created them.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'chatddx_writer') THEN
        CREATE ROLE chatddx_writer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'chatddx_reader') THEN
        CREATE ROLE chatddx_reader NOLOGIN;
    END IF;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA factor, ledger FROM PUBLIC;
REVOKE ALL ON SCHEMA factor, ledger FROM PUBLIC;

GRANT USAGE ON SCHEMA factor, ledger TO chatddx_writer;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA factor, ledger TO chatddx_writer;

GRANT USAGE ON SCHEMA factor TO chatddx_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA factor TO chatddx_reader;
