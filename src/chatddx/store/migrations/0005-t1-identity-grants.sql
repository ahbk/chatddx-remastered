REVOKE ALL ON ALL TABLES IN SCHEMA identity FROM PUBLIC;
REVOKE ALL ON SCHEMA identity FROM PUBLIC;

GRANT USAGE ON SCHEMA identity TO chatddx_writer, chatddx_reader;

-- No default privileges: tables here may be mutable, so each one is granted explicitly.
GRANT SELECT, INSERT, UPDATE ON identity.person TO chatddx_writer;
GRANT SELECT ON identity.person TO chatddx_reader;
